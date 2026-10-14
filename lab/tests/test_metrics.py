import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labkit import metrics  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
KEYS = (
    "ctx_start", "ctx_end", "ctx_growth", "api_calls", "turns", "tokens_in",
    "tokens_out", "cache_read", "cache_write", "api_equiv_usd", "sub_calls",
    "sub_ctx_start_max", "sub_ctx_growth_max",
)  # fmt: skip


class Fixture(unittest.TestCase):
    """Each test works on a throwaway copy of a config-dir-shaped fixture.

    `fixture_copies` is a temp dir that holds those copies, one per
    subdirectory; tests that need an empty or hand-written config dir use
    the temp dir itself.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture_copies = Path(self.tmp.name)

    def config(self, name):
        dst = self.fixture_copies / name
        shutil.copytree(FIXTURES / name, dst)
        return dst

    def compute(self, name):
        return metrics.compute(self.config(name))


class TestCompute(Fixture):
    def test_headless_single(self):
        m = self.compute("headless_single")
        self.assertEqual(m["ctx_start"], 17947)
        self.assertEqual(m["ctx_end"], 17947)
        self.assertEqual(m["ctx_growth"], 0)
        self.assertEqual(m["api_calls"], 1)
        self.assertEqual(m["turns"], 1)

    def test_dedup_and_context(self):
        m = self.compute("headless_subagent")
        self.assertEqual(m["api_calls"], 3)
        self.assertEqual(m["ctx_start"], 18758)
        self.assertEqual(m["ctx_end"], 19753)
        self.assertEqual(m["ctx_growth"], 995)

    def test_turns_ignore_system_notifications(self):
        self.assertEqual(self.compute("headless_subagent")["turns"], 1)

    def test_totals_from_cost_state(self):
        m = self.compute("headless_subagent")
        self.assertEqual(m["tokens_in"], 14)
        self.assertEqual(m["tokens_out"], 880)
        self.assertEqual(m["cache_read"], 49104)
        self.assertEqual(m["cache_write"], 31207)
        self.assertGreater(m["api_equiv_usd"], 0)

    def test_totals_sum_per_process(self):
        cfg = self.config("headless_subagent")
        main = next((cfg / "projects").glob("*/*.jsonl"))
        lines = main.read_text().splitlines()
        state = json.loads(next(x for x in lines if '"cost-state"' in x))
        base = metrics.compute(cfg)
        state["startTime"] += 1  # a second `claude` process in the same session
        with main.open("a") as fh:
            fh.write(json.dumps(state) + "\n")
        m = metrics.compute(cfg)
        for key in ("tokens_in", "tokens_out", "cache_read", "cache_write"):
            self.assertEqual(m[key], 2 * base[key], key)
        self.assertAlmostEqual(m["api_equiv_usd"], 2 * base["api_equiv_usd"])

    def test_same_start_time_counts_once(self):
        """Interactive sessions write cost-state repeatedly; only the last counts."""
        m = self.compute("interactive_single")
        # two identical cost-state entries (20 output tokens each), counted once
        self.assertEqual(m["tokens_out"], 20)
        self.assertEqual(m["cache_write"], 32468)

    def test_subagents(self):
        m = self.compute("headless_subagent")
        self.assertEqual(m["sub_calls"], 2)
        self.assertEqual(m["sub_ctx_start_max"], 11007)
        self.assertEqual(m["sub_ctx_growth_max"], 455)

    def test_no_subagents(self):
        m = self.compute("headless_single")
        self.assertEqual(m["sub_calls"], 0)
        self.assertIsNone(m["sub_ctx_start_max"])
        self.assertIsNone(m["sub_ctx_growth_max"])

    def test_subagent_calls_do_not_leak_into_main_thread(self):
        m = self.compute("headless_subagent")
        self.assertEqual(m["api_calls"], 3)  # not 3 + 2

    def test_interactive_single(self):
        m = self.compute("interactive_single")
        self.assertEqual(m["ctx_start"], 31115)
        self.assertEqual(m["turns"], 1)

    def test_aborted_has_no_totals(self):
        m = self.compute("aborted")
        self.assertIsNone(m["tokens_out"])
        self.assertIsNone(m["api_equiv_usd"])
        self.assertEqual(m["ctx_start"], 18758)

    def test_empty_config_dir(self):
        m = metrics.compute(self.fixture_copies)
        self.assertEqual(set(m), set(KEYS))
        self.assertTrue(all(v is None for v in m.values()), m)

    def test_missing_config_dir(self):
        m = metrics.compute(self.fixture_copies / "nope")
        self.assertEqual(set(m), set(KEYS))
        self.assertTrue(all(v is None for v in m.values()), m)

    def test_garbage_lines_do_not_raise(self):
        cfg = self.config("headless_single")
        main = next((cfg / "projects").glob("*/*.jsonl"))
        with main.open("a") as fh:
            fh.write("not json\n[1, 2]\n{\"type\": \"assistant\", \"message\": 5}\n\n")
        m = metrics.compute(cfg)
        self.assertEqual(m["api_calls"], 1)
        self.assertEqual(m["ctx_start"], 17947)


class TestKeepLast(Fixture):
    """Which entry wins when Claude Code logs the same thing more than once."""

    @staticmethod
    def assistant(msg_id, input_tokens, sidechain=False):
        return {
            "type": "assistant", "isSidechain": sidechain, "timestamp": "2026-10-12T19:00:00.000Z",
            "message": {"id": msg_id, "model": "claude-haiku-5-5", "usage": {
                "input_tokens": input_tokens, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            }},
        }  # fmt: skip

    @staticmethod
    def cost_state(start, output_tokens):
        return {"type": "cost-state", "startTime": start, "totalCostUSD": output_tokens / 1000,
                "modelUsage": {"claude-haiku-5-5": {"inputTokens": 1, "outputTokens": output_tokens,
                                                    "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}}}

    def write(self, *entries):
        proj = self.fixture_copies / "projects" / "p"
        proj.mkdir(parents=True)
        (proj / "s.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))
        return metrics.compute(self.fixture_copies)

    def test_last_entry_of_a_message_id_wins(self):
        # One response logged per content block; the later entries carry the final usage.
        m = self.write(self.assistant("msg_1", 100), self.assistant("msg_1", 250), self.assistant("msg_2", 400),
                       self.assistant("msg_2", 420))
        self.assertEqual(m["api_calls"], 2)
        self.assertEqual((m["ctx_start"], m["ctx_end"], m["ctx_growth"]), (250, 420, 170))

    def test_last_cost_state_of_a_start_time_wins(self):
        m = self.write(self.cost_state(1000, 10), self.cost_state(1000, 35), self.cost_state(2000, 7))
        self.assertEqual(m["tokens_out"], 35 + 7)
        self.assertEqual(m["tokens_in"], 2)
        self.assertAlmostEqual(m["api_equiv_usd"], 0.042)

    def test_sidechain_entries_in_the_main_transcript_do_not_count(self):
        m = self.write(self.assistant("msg_1", 100), self.assistant("msg_2", 300),
                       self.assistant("msg_side", 9000, sidechain=True))
        self.assertEqual(m["api_calls"], 2)
        self.assertEqual(m["ctx_end"], 300)


class TestSyntheticEntries(Fixture):
    """Claude Code's own `<synthetic>` assistant messages are not API calls."""

    @staticmethod
    def synthetic(msg_id, sidechain):
        return {
            "type": "assistant",
            "timestamp": "2026-10-12T19:00:00.000Z",
            "isSidechain": sidechain,
            "message": {
                "id": msg_id,
                "model": "<synthetic>",
                "role": "assistant",
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        }

    def append(self, path, entry):
        with path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def test_synthetic_entries_ignored(self):
        cfg = self.config("headless_subagent")
        main = next((cfg / "projects").glob("*/*.jsonl"))
        sub = next((cfg / "projects").glob("*/*/subagents/agent-*.jsonl"))
        self.append(main, self.synthetic("msg_synth_main", False))
        self.append(sub, self.synthetic("msg_synth_sub", True))
        m = metrics.compute(cfg)
        self.assertEqual(m["api_calls"], 3)
        self.assertEqual(m["ctx_start"], 18758)
        self.assertEqual(m["ctx_end"], 19753)
        self.assertEqual(m["ctx_growth"], 995)
        self.assertEqual(m["sub_calls"], 2)
        self.assertEqual(m["sub_ctx_start_max"], 11007)
        self.assertEqual(m["sub_ctx_growth_max"], 455)

    def test_entry_without_input_usage_ignored(self):
        cfg = self.config("headless_single")
        main = next((cfg / "projects").glob("*/*.jsonl"))
        entry = self.synthetic("msg_no_usage", False)
        entry["message"]["model"] = "claude-haiku-5-5"
        entry["message"]["usage"] = {"output_tokens": 0}
        self.append(main, entry)
        entry["message"]["id"], entry["message"]["usage"] = "msg_null", None
        self.append(main, entry)
        m = metrics.compute(cfg)
        self.assertEqual(m["api_calls"], 1)
        self.assertEqual(m["ctx_growth"], 0)

    def test_real_call_with_zero_field_still_counts(self):
        cfg = self.config("headless_single")
        main = next((cfg / "projects").glob("*/*.jsonl"))
        entry = self.synthetic("msg_real", False)
        entry["message"]["model"] = "claude-haiku-5-5"
        entry["message"]["usage"] = {"input_tokens": 3, "cache_read_input_tokens": 0}
        self.append(main, entry)
        m = metrics.compute(cfg)
        self.assertEqual(m["api_calls"], 2)
        self.assertEqual(m["ctx_end"], 3)


class TestMainTranscripts(Fixture):
    def test_sorted_by_first_timestamp(self):
        proj = self.fixture_copies / "projects" / "p"
        proj.mkdir(parents=True)
        (proj / "a.jsonl").write_text('{"type": "x", "timestamp": "2026-01-02T00:00:00Z"}\n')
        (proj / "b.jsonl").write_text(
            '{"type": "no-timestamp"}\n{"type": "x", "timestamp": "2026-01-01T00:00:00Z"}\n'
        )
        (proj / "c.jsonl").write_text("")
        self.assertEqual(
            [p.name for p in metrics.main_transcripts(self.fixture_copies)],
            ["b.jsonl", "a.jsonl", "c.jsonl"],
        )

    def test_excludes_subagent_files(self):
        cfg = self.config("headless_subagent")
        names = [p.name for p in metrics.main_transcripts(cfg)]
        self.assertEqual(names, ["8f01236c-d084-4e20-a838-6504e25a4062.jsonl"])

    def test_missing_dir(self):
        self.assertEqual(metrics.main_transcripts(self.fixture_copies / "nope"), [])


class TestGitBranches(Fixture):
    def test_branches_from_main_transcripts(self):
        cfg = self.config("headless_subagent")
        (main,) = cfg.glob("projects/*/*.jsonl")
        entries = [json.loads(line) for line in main.read_text().splitlines()]
        entries[0]["gitBranch"] = "feature/x"
        entries[1]["gitBranch"] = "HEAD"
        entries[2]["gitBranch"] = 7  # not a string: ignored
        main.write_text("".join(json.dumps(e) + "\n" for e in entries) + "not json\n")
        self.assertEqual(metrics.git_branches(cfg), {"feature/x", "HEAD"})

    def test_no_branch_fields(self):
        self.assertEqual(metrics.git_branches(self.config("headless_subagent")), set())

    def test_missing_dir(self):
        self.assertEqual(metrics.git_branches(self.fixture_copies / "nope"), set())


class TestStreamInfo(unittest.TestCase):
    def test_stream_info(self):
        info = metrics.stream_info([FIXTURES / "headless_subagent" / "stream.jsonl"])
        self.assertEqual(info["limit_5h_end_pct"], 12)
        self.assertIs(info["denied"], False)
        self.assertEqual(info["session_id"], "8f01236c-d084-4e20-a838-6504e25a4062")

    def test_result_totals_take_last_result_per_session(self):
        # three cumulative result events in one file: counted once, not thrice
        info = metrics.stream_info([FIXTURES / "headless_subagent" / "stream.jsonl"])
        self.assertEqual(
            info["result_totals"],
            {"tokens_in": 14, "tokens_out": 880, "cache_read": 49104, "cache_write": 31207},
        )

    def test_result_totals_sum_over_files(self):
        one = FIXTURES / "headless_subagent" / "stream.jsonl"
        two = FIXTURES / "headless_single" / "stream.jsonl"
        info = metrics.stream_info([one, two])
        self.assertEqual(info["result_totals"]["tokens_in"], 14 + 2)
        self.assertEqual(info["result_totals"]["tokens_out"], 880 + 4)
        self.assertEqual(info["result_totals"]["cache_write"], 31207 + 17945)
        self.assertEqual(info["limit_5h_end_pct"], 11)  # last rate_limit_event wins

    @staticmethod
    def result(session, out):
        return json.dumps(
            {
                "type": "result",
                "session_id": session,
                "modelUsage": {"m": {"inputTokens": 1, "outputTokens": out}},
            }
        )

    def streams(self, tmp, *sessions_and_outs):
        """One stream file per (session, outputTokens) pair, in order."""
        files = []
        for i, (session, out) in enumerate(sessions_and_outs):
            f = Path(tmp) / f"stream-{i + 1}.jsonl"
            f.write_text(self.result(session, out) + "\n")
            files.append(f)
        return files

    def test_result_totals_resumed_session_counted_once(self):
        # `claude -p --resume` keeps the session id and continues its totals
        with tempfile.TemporaryDirectory() as tmp:
            files = self.streams(tmp, ("s1", 10), ("s1", 25), ("s1", 40))
            totals = metrics.stream_info(files)["result_totals"]
        self.assertEqual(totals["tokens_out"], 40)
        self.assertEqual(totals["tokens_in"], 1)

    def test_result_totals_distinct_sessions_add_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = self.streams(tmp, ("s1", 10), ("s2", 7))
            totals = metrics.stream_info(files)["result_totals"]
        self.assertEqual(totals["tokens_out"], 17)

    def test_result_totals_interleaved_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = self.streams(tmp, ("s1", 10), ("s2", 7), ("s1", 25))
            totals = metrics.stream_info(files)["result_totals"]
        self.assertEqual(totals["tokens_out"], 25 + 7)

    def test_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "stream.jsonl"
            f.write_text(
                '{"type": "result", "session_id": "s", "permission_denials": [{"tool_name": "Bash"}]}\n'
            )
            self.assertIs(metrics.stream_info([f])["denied"], True)

    def test_empty_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "stream.jsonl"
            empty.write_text("")
            for files in ([], [empty], [Path(tmp) / "nope.jsonl"]):
                info = metrics.stream_info(files)
                self.assertEqual(
                    set(info),
                    {"session_id", "limit_5h_end_pct", "denied", "result_totals", "mcp_connected"},
                )
                self.assertEqual(info.pop("mcp_connected"), "")
                self.assertTrue(all(v is None for v in info.values()), info)


class TestMcpConnected(unittest.TestCase):
    """`mcp_connected` from the first `system`/`init` event of the first stream file."""

    @staticmethod
    def init(*statuses):
        servers = [{"name": f"s{i}", "status": status} for i, status in enumerate(statuses)]
        return json.dumps({"type": "system", "subtype": "init", "session_id": "s", "mcp_servers": servers})

    def connected(self, *files_lines):
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for i, lines in enumerate(files_lines):
                f = Path(tmp) / f"stream-{i + 1}.jsonl"
                f.write_text("".join(line + "\n" for line in lines))
                files.append(f)
            return metrics.stream_info(files)["mcp_connected"]

    def test_connected_pending_mix(self):
        line = self.init("connected", "pending", "connected", "failed", "pending")
        self.assertEqual(self.connected([line]), "2/5")

    def test_first_init_of_the_first_turn_only(self):
        first, later = self.init("connected", "pending"), self.init("connected", "connected")
        self.assertEqual(self.connected([first, later], [self.init("connected")]), "1/2")
        # The second turn's init does not count, even when the first turn has none.
        self.assertEqual(self.connected(['{"type": "result"}'], [later]), "")

    def test_no_servers_is_empty(self):
        self.assertEqual(self.connected([self.init()]), "")
        no_key = json.dumps({"type": "system", "subtype": "init", "session_id": "s"})
        self.assertEqual(self.connected([no_key]), "")

    def test_no_init_event_is_empty(self):
        other = json.dumps({"type": "system", "subtype": "status", "mcp_servers": [{"status": "connected"}]})
        self.assertEqual(self.connected([other, '{"type": "result"}']), "")


if __name__ == "__main__":
    unittest.main()
