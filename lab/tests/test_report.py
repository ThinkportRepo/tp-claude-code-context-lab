import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labkit import report, results  # noqa: E402


def row(**kw):
    """A run row with the grouping keys filled in; kw overrides."""
    base = {
        "mode": "headless", "model": "m1", "profile": "clean",
        "prompt": "simple", "status": "ok",
    }  # fmt: skip
    base.update(kw)
    return base


def group(rows, **match):
    """The one summary group whose keys equal match."""
    found = [g for g in report.summarize(rows) if all(g[k] == v for k, v in match.items())]
    assert len(found) == 1, found
    return found[0]


class TestResultsFile(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "results.csv"

    def test_goal_ok_replaces_pages_ok_in_place(self):
        self.assertEqual(results.FIELDS[results.FIELDS.index("status") + 1], "goal_ok")
        self.assertNotIn("pages_ok", results.FIELDS)

    def test_append_rejects_unknown_field(self):
        with self.assertRaises(ValueError):
            results.append(self.path, {"mode": "headless", "bogus": 1})
        self.assertFalse(self.path.exists())

    def test_roundtrip(self):
        results.append(self.path, row(
            timestamp="20261012T101500Z", ctx_start=18758, api_equiv_usd=0.0123,
            ctx_end=None, goal_ok=True,
            plugin_versions={"b": "2.0", "a": "1.0.1"},
        ))  # fmt: skip
        results.append(self.path, row(plugin_versions="x@1", ctx_start=5))
        lines = self.path.read_text().splitlines()
        self.assertEqual(lines[0], ",".join(results.FIELDS))
        self.assertEqual(sum(l.startswith("timestamp,") for l in lines), 1)  # one header
        first, second = results.read(self.path)
        self.assertEqual(list(first), list(results.FIELDS))
        self.assertEqual(first["ctx_start"], "18758")
        self.assertEqual(first["api_equiv_usd"], "0.0123")
        self.assertEqual(first["ctx_end"], "")  # None -> empty cell
        self.assertEqual(first["tokens_out"], "")  # missing key -> empty cell
        self.assertEqual(first["goal_ok"], "True")
        self.assertEqual(first["plugin_versions"], "a@1.0.1;b@2.0")
        self.assertEqual(second["plugin_versions"], "x@1")

    def test_append_refuses_a_file_with_another_header(self):
        old = "timestamp,test,mode,old_column\n20261001T000000Z,test1,headless,7\n"
        self.path.write_text(old)
        with self.assertRaises(ValueError) as ctx:
            results.append(self.path, row(ctx_start=1))
        message = str(ctx.exception)
        self.assertIn(str(self.path), message)
        self.assertIn("results-", message)  # where to move the old file
        self.assertEqual(self.path.read_text(), old)  # nothing appended

    def test_append_accepts_an_empty_file(self):
        self.path.write_text("")
        results.append(self.path, row(ctx_start=1))
        self.assertEqual(results.read(self.path)[0]["ctx_start"], "1")

    def test_read_missing_file_is_empty(self):
        self.assertEqual(results.read(self.path), [])

    def test_empty_plugin_versions_is_empty_cell(self):
        results.append(self.path, row(plugin_versions={}))
        self.assertEqual(results.read(self.path)[0]["plugin_versions"], "")


class TestSummarize(unittest.TestCase):
    def test_summarize_median_min_max(self):
        g = group([row(ctx_start=v) for v in (10, 20, 60)])
        self.assertEqual(g["ctx_start"], {"median": 20, "min": 10, "max": 60})
        self.assertEqual(g["n"], 3)

    def test_summarize_reads_csv_strings(self):
        g = group([row(ctx_start="10"), row(ctx_start="30")])
        self.assertEqual(g["ctx_start"], {"median": 20, "min": 10, "max": 30})

    def test_summarize_skips_empty(self):
        g = group([row(ctx_start=10), row(ctx_start=""), row(ctx_start=None), row()])
        self.assertEqual(g["ctx_start"], {"median": 10, "min": 10, "max": 10})
        self.assertEqual(g["n"], 4)
        self.assertIsNone(g["ctx_end"])  # no value anywhere

    def test_ok_and_goal_ok_counts(self):
        g = group([
            row(status="ok", goal_ok="True"), row(status="failed", goal_ok="False"),
            row(status="ok", goal_ok=True), row(status="timeout", goal_ok=""),
        ])  # fmt: skip
        self.assertEqual((g["n"], g["ok"], g["goal_ok"]), (4, 2, 2))

    def test_limit_used_is_end_minus_start(self):
        g = group([row(limit_5h_start_pct=12, limit_5h_end_pct=15)])
        self.assertEqual(g["limit_used"], {"median": 3, "min": 3, "max": 3})

    def test_limit_used_skips_rows_missing_either_end(self):
        g = group([
            row(limit_5h_start_pct="12", limit_5h_end_pct="15"),
            row(limit_5h_start_pct="", limit_5h_end_pct="40"),
            row(limit_5h_start_pct="20", limit_5h_end_pct=""),
        ])  # fmt: skip
        # Taking a missing end as 0 would give 40 and -20: same median, other min and max.
        self.assertEqual(g["limit_used"], {"median": 3, "min": 3, "max": 3})

    def test_groups_keep_modes_apart(self):
        rows = [
            row(mode="interactive", ctx_start=31115),
            row(mode="headless", ctx_start=18758),
            row(mode="headless", ctx_start=18760, prompt="other"),
        ]
        groups = report.summarize(rows)
        self.assertEqual(len(groups), 3)
        self.assertEqual(group(rows, mode="interactive")["ctx_start"]["median"], 31115)
        self.assertEqual(group(rows, mode="headless", prompt="simple")["ctx_start"]["median"], 18758)

    def test_groups_sorted_by_mode_model_profile_prompt(self):
        rows = [
            row(mode="interactive"), row(model="m2"), row(prompt="z"),
            row(profile="dev"), row(),
        ]  # fmt: skip
        keys = [(g["mode"], g["model"], g["profile"], g["prompt"]) for g in report.summarize(rows)]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(keys[0], ("headless", "m1", "clean", "simple"))


class TestStartingPoints(unittest.TestCase):
    def test_starting_points_three_contexts(self):
        rows = [
            row(mode="interactive", ctx_start=31115, ctx_growth=500),
            row(mode="interactive", ctx_start=31117, ctx_growth=700),
            row(mode="headless", ctx_start=18758, ctx_growth=995,
                sub_ctx_start_max=11007, sub_ctx_growth_max=455),
            row(mode="headless", profile="dev", ctx_start=40000, ctx_growth=1),
        ]  # fmt: skip
        points = {(p["profile"], p["model"]): p for p in report.starting_points(rows)}
        self.assertEqual(len(points), 2)
        p = points[("clean", "m1")]
        self.assertEqual(p["interactive_ctx_start"], 31116)
        self.assertEqual(p["interactive_ctx_growth"], 600)
        self.assertEqual(p["headless_ctx_start"], 18758)
        self.assertEqual(p["headless_ctx_growth"], 995)
        self.assertEqual(p["sub_ctx_start_max"], 11007)
        self.assertEqual(p["sub_ctx_growth_max"], 455)
        d = points[("dev", "m1")]
        self.assertIsNone(d["interactive_ctx_start"])
        self.assertIsNone(d["sub_ctx_start_max"])


class TestFmt(unittest.TestCase):
    def test_boundaries(self):
        cases = {
            None: "–", 0: "0", 999: "999", 1000: "1.0k", 31115: "31.1k",
            99_999: "100k", 100_000: "100k", 450_000: "450k", 999_999: "1.0M",
            1_000_000: "1.0M", 1_234_600: "1.2M", -1500: "-1.5k",
        }  # fmt: skip
        for value, text in cases.items():
            self.assertEqual(report._fmt(value), text, value)


class TestRender(unittest.TestCase):
    def test_render_hides_minmax_for_single_run(self):
        text = report.render([row(ctx_start=18758)])
        self.assertIn("18.8k", text)
        self.assertNotIn("–18", text)
        self.assertNotIn("(", text)

    def test_render_shows_minmax_for_several_runs(self):
        rows = [row(ctx_start=v) for v in (17900, 18800, 19700)]
        self.assertIn("18.8k (17.9k–19.7k)", report.render(rows))

    def test_render_formats_small_values_and_missing(self):
        lines = report.render([row(ctx_start=950, tokens_out=0)]).splitlines()
        header = next(l for l in lines if l.startswith("profile")).split()
        cells = next(l for l in lines if l.startswith("clean")).split()
        self.assertEqual(header[5:], ["ctx_start", "ctx_growth", "tokens_out", "cache_read", "limit_used"])
        self.assertEqual(cells[5:], ["950", "–", "0", "–", "–"])

    def test_render_shows_goal_count_next_to_ok(self):
        rows = [
            row(status="ok", goal_ok="True"), row(status="ok", goal_ok="True"),
            row(status="failed", goal_ok="False"),
        ]  # fmt: skip
        lines = report.render(rows).splitlines()
        header = next(l for l in lines if l.startswith("profile")).split()
        cells = next(l for l in lines if l.startswith("clean")).split()
        self.assertEqual(header[2:5], ["n", "ok", "goal"])
        # n=3, ok=2, goal=2: a column showing n or the wrong count would differ.
        self.assertEqual(cells[2:5], ["3", "2", "2"])
        rows[0]["goal_ok"] = "False"
        cells = next(l for l in report.render(rows).splitlines() if l.startswith("clean")).split()
        self.assertEqual(cells[2:5], ["3", "2", "1"])

    def assert_fits(self, cache_reads):
        rows = [
            row(mode=m, profile="superpowers", prompt="well-defined",
                model="claude-sonnet-5-5", ctx_start=v, ctx_growth=v // 10, tokens_out=v // 5,
                cache_read=c, limit_5h_start_pct=10, limit_5h_end_pct=10 + i)
            for m in ("headless", "interactive")
            for i, (v, c) in enumerate(zip((17900, 18800, 123456), cache_reads))
        ]  # fmt: skip
        for line in report.render(rows).splitlines():
            self.assertLessEqual(len(line), 120, line)

    def test_render_fits_live_terminal(self):
        self.assert_fits((350_000, 420_000, 610_000))

    def test_render_fits_live_terminal_with_millions(self):
        self.assert_fits((400_000, 700_000, 1_234_600))

    def test_render_has_all_tables_and_handles_no_rows(self):
        text = report.render([row(ctx_start=1)])
        self.assertIn("profile", text)
        self.assertIn("subagent", text)
        self.assertIn("Token consumption", text)
        self.assertIsInstance(report.render([]), str)


USAGE = {"tokens_in": 14, "cache_write": 31207, "cache_read": 49104, "tokens_out": 880}


class TestTokenTable(unittest.TestCase):
    """Table 3: token consumption per group."""

    @staticmethod
    def table(rows):
        """Lines of the token table: from its title to the end of the report."""
        lines = report.render(rows).splitlines()
        start = next(i for i, l in enumerate(lines) if l.startswith("Token consumption"))
        return lines[start:]

    def test_total_is_the_sum_of_the_four_kinds(self):
        g = group([row(**USAGE), row(tokens_in=1, cache_write=2, cache_read=3, tokens_out=4)])
        self.assertEqual(g["tokens_total"], {"median": (81205 + 10) / 2, "min": 10, "max": 81205})
        self.assertIsNone(group([row(tokens_in=5)])["tokens_total"])  # parts missing: no total

    def test_title_says_what_total_is(self):
        self.assertIn("total = input + cache write + cache read + output", self.table([row(**USAGE)])[0])

    def test_single_run_cells(self):
        lines = self.table([row(**USAGE, api_equiv_usd=0.009)])
        header = next(l for l in lines if l.startswith("profile"))
        names = ["profile", "prompt", "n", "input", "cache write", "cache read", "output", "total", "API-equiv"]
        positions = [header.find(f" {n}") if i else 0 for i, n in enumerate(names)]
        self.assertNotIn(-1, positions, header)
        self.assertEqual(positions, sorted(positions), header)
        self.assertIn("[headless / m1]", lines)
        cells = next(l for l in lines if l.startswith("clean")).split()
        self.assertEqual(cells, ["clean", "simple", "1", "14", "31.2k", "49.1k", "880", "81.2k", "$0.009"])

    def test_api_equiv_keeps_three_decimals(self):
        cells = next(l for l in self.table([row(**USAGE, api_equiv_usd=0.0004)]) if l.startswith("clean"))
        self.assertTrue(cells.endswith("$0.000"), cells)
        cells = next(l for l in self.table([row(**USAGE, api_equiv_usd=0.01164)]) if l.startswith("clean"))
        self.assertTrue(cells.endswith("$0.012"), cells)

    def test_several_runs_show_min_max_below_the_median(self):
        rows = [row(**USAGE, api_equiv_usd=0.009), row(**(USAGE | {"tokens_out": 1200}), api_equiv_usd=0.012),
                row(**(USAGE | {"tokens_out": 700}), api_equiv_usd=0.01)]
        lines = self.table(rows)
        i = next(i for i, l in enumerate(lines) if l.startswith("clean"))
        self.assertEqual(lines[i].split()[2:4], ["3", "14"])
        below = lines[i + 1].split()
        self.assertFalse(lines[i + 1].startswith("clean"))
        self.assertEqual(below[0], "14–14")  # input
        self.assertIn("700–1.2k", below)  # output
        self.assertEqual(below[-1], "$0.009–$0.012")

    def test_missing_usage_is_a_dash(self):
        cells = next(l for l in self.table([row(ctx_start=5)]) if l.startswith("clean")).split()
        self.assertEqual(cells[3:], ["–"] * 6)

    def test_fits_live_terminal_with_realistic_magnitudes(self):
        # From dev runs: Haiku sessions with 350k–730k cache reads; plus a 1.2M one.
        rows = [
            row(mode=m, profile="superpowers", prompt="well-defined", model="claude-sonnet-5-5",
                tokens_in=i, cache_write=w, cache_read=r, tokens_out=o, api_equiv_usd=c)
            for m in ("headless", "interactive")
            for i, w, r, o, c in ((28, 12604, 401_405, 4971, 0.009), (40, 16493, 725_552, 7126, 0.0117),
                                  (60, 19733, 1_234_600, 7414, 0.0147))
        ]  # fmt: skip
        lines = self.table(rows)
        self.assertIn("1.3M", "\n".join(lines))  # total up to ~1.3M
        for line in report.render(rows).splitlines():
            self.assertLessEqual(len(line), 120, line)


if __name__ == "__main__":
    unittest.main()
