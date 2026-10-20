import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labkit import config, tokens  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PROFILES = ("clean", "superpowers", "dev-stack", "dev-messy")


class TempLab(unittest.TestCase):
    """A throwaway lab root with lab/profiles/<name>/ directories and the two prompt files."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.lab_root = Path(self.dir.name).resolve() / "lab-root"
        for name in PROFILES:
            (self.lab_root / "lab" / "profiles" / name).mkdir(parents=True)
        for name in ("prompt-simple.md", "prompt-well-defined.md"):
            (self.lab_root / name).write_text(f"{name} text\n")
        self.matrix = self.lab_root / "lab" / "matrix.conf"

    def tearDown(self):
        self.dir.cleanup()

    def load(self, text, environ=None):
        self.matrix.write_text(text)
        return config.load_matrix(self.matrix, environ or {})


class Matrix(TempLab):
    def test_matrix_model_precedence(self):
        text = "test1 clean prompt-simple.md m-col\ntest2 clean prompt-well-defined.md\n"
        tests = self.load(text, {"LAB_MODEL": "m-env"})
        self.assertEqual([t.model for t in tests], ["m-col", "m-env"])
        tests = self.load(text, {})
        self.assertEqual([t.model for t in tests], ["m-col", config.DEFAULT_MODEL])
        tests = self.load(text, {"LAB_MODEL": ""})
        self.assertEqual(tests[1].model, config.DEFAULT_MODEL)
        self.assertEqual(config.DEFAULT_MODEL, "claude-haiku-5-5")

    def test_matrix_parses_columns_and_skips_comments(self):
        tests = self.load("# id profile prompt\n\n  test1\tclean   prompt-simple.md\n")
        self.assertEqual(tests, [config.Test("test1", "clean", "prompt-simple.md", config.DEFAULT_MODEL)])

    def test_matrix_error_names_line(self):
        with self.assertRaises(ValueError) as ctx:
            self.load("# header\ntest1 clean prompt-simple.md\ntest2\n")
        self.assertIn("matrix.conf:3", str(ctx.exception))
        self.assertIn(str(self.matrix), str(ctx.exception))

    def test_too_many_columns_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.load("test1 clean prompt-simple.md model high extra\n")
        self.assertIn("matrix.conf:1", str(ctx.exception))
        self.assertIn("[effort]", str(ctx.exception))

    def test_effort_column(self):
        text = (
            "test1 clean prompt-simple.md\n"
            "test2 clean prompt-simple.md m-col high\n"
            "test3 clean prompt-simple.md - medium\n"
            "test4 clean prompt-simple.md -\n"
        )
        tests = self.load(text, {"LAB_MODEL": "m-env"})
        self.assertEqual(
            [(t.model, t.effort) for t in tests],
            [("m-env", None), ("m-col", "high"), ("m-env", "medium"), ("m-env", None)],
        )
        self.assertEqual(self.load("test1 clean prompt-simple.md - low\n")[0].model, config.DEFAULT_MODEL)

    def test_every_effort_level_parses(self):
        for level in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=level):
                self.assertEqual(self.load(f"test1 clean prompt-simple.md - {level}\n")[0].effort, level)

    def test_invalid_effort_rejected(self):
        for bad in ("bogus", "Medium", "-"):
            with self.subTest(effort=bad):
                with self.assertRaises(ValueError) as ctx:
                    self.load(f"# header\ntest1 clean prompt-simple.md m {bad}\n")
                self.assertIn(f"{self.matrix}:2", str(ctx.exception))
                self.assertIn("effort", str(ctx.exception))

    def test_effort_defaults_to_none(self):
        self.assertIsNone(config.Test("test1", "clean", "prompt-simple.md", "m").effort)

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.load("test1 clean prompt-simple.md\ntest2 nope prompt-simple.md\n")
        self.assertIn("matrix.conf:2", str(ctx.exception))
        self.assertIn("nope", str(ctx.exception))

    def test_prompt_column_is_a_file_in_the_lab_root(self):
        (self.lab_root / "prompts").mkdir()
        (self.lab_root / "prompts" / "mine.md").write_text("mine\n")
        text = "test1 clean prompt-well-defined.md\ntest2 clean prompts/mine.md m high\n"
        self.assertEqual([t.prompt for t in self.load(text)], ["prompt-well-defined.md", "prompts/mine.md"])

    def test_prompt_dash_or_missing_column_means_default(self):
        text = "test1 clean\ntest2 clean -\ntest3 clean - m-col high\n"
        tests = self.load(text, {"LAB_MODEL": "m-env"})
        self.assertEqual(config.DEFAULT_PROMPT, "prompt-simple.md")
        self.assertEqual(
            [(t.prompt, t.model, t.effort) for t in tests],
            [("prompt-simple.md", "m-env", None), ("prompt-simple.md", "m-env", None),
             ("prompt-simple.md", "m-col", "high")],
        )  # fmt: skip

    def test_prompt_file_must_exist(self):
        for bad in ("poem.md", "lab", "lab/profiles"):  # missing, or a directory
            with self.subTest(prompt=bad):
                with self.assertRaises(ValueError) as ctx:
                    self.load(f"# header\ntest1 clean {bad}\n")
                self.assertIn(f"{self.matrix}:2", str(ctx.exception))
                self.assertIn(repr(bad), str(ctx.exception))

    def test_prompt_file_must_be_inside_the_lab_root(self):
        outside = self.lab_root.parent / "x.md"
        outside.write_text("outside\n")
        (self.lab_root / "link.md").symlink_to(outside)
        for bad in ("../x.md", str(outside), "link.md", "lab/../../x.md"):
            with self.subTest(prompt=bad):
                with self.assertRaises(ValueError) as ctx:
                    self.load(f"test1 clean {bad}\n")
                self.assertIn(f"{self.matrix}:1", str(ctx.exception))
                self.assertIn(repr(bad), str(ctx.exception))
                self.assertIn("lab root", str(ctx.exception))

    def test_old_prompt_names_rejected(self):
        for old in ("simple", "well-defined"):
            with self.subTest(prompt=old):
                with self.assertRaises(ValueError) as ctx:
                    self.load(f"test1 clean {old}\n")
                self.assertIn("matrix.conf:1", str(ctx.exception))
                self.assertIn(repr(old), str(ctx.exception))
                self.assertIn("file", str(ctx.exception))
                self.assertIn("prompt-simple.md", str(ctx.exception))

    def test_duplicate_id_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.load("test1 clean prompt-simple.md\ntest1 superpowers prompt-simple.md\n")
        self.assertIn("matrix.conf:2", str(ctx.exception))
        self.assertIn("test1", str(ctx.exception))

    def test_test_ids_must_be_lab_test_names(self):
        # Each id becomes sandbox/<id>/ (wiped by every run) and repo lab-<id>.
        for bad in ("/tmp/victim", "../..", "..", "t1", "test1/../x", "test/x", "Test1", "test.1"):
            with self.subTest(id=bad):
                with self.assertRaises(ValueError) as ctx:
                    self.load(f"{bad} clean prompt-simple.md\n")
                self.assertIn("matrix.conf:1", str(ctx.exception))
        ids = [t.id for t in self.load("test clean prompt-simple.md\ntest_a-B9 clean prompt-simple.md\n")]
        self.assertEqual(ids, ["test", "test_a-B9"])

    def test_profile_names_must_be_plain(self):
        (self.lab_root / "lab" / "settings.json").write_text("{}")  # what `..` would reach
        for bad in ("..", ".", "Clean", "-x", "clean/../clean"):
            with self.subTest(profile=bad), self.assertRaises(ValueError):
                self.load(f"test1 {bad} prompt-simple.md\n")


class Prompts(unittest.TestCase):
    def test_prompt_text_reads_the_file_unchanged(self):
        for name in ("prompt-simple.md", "prompt-well-defined.md"):
            with self.subTest(prompt=name):
                self.assertEqual(config.prompt_text(REPO, name), (REPO / name).read_text())

    def test_default_prompt_is_a_repo_file(self):
        self.assertTrue((REPO / config.DEFAULT_PROMPT).is_file())

    def test_prompt_text_rejects_missing_or_outside_files(self):
        for bad in ("poem.md", "simple", "lab", "../x.md"):
            with self.subTest(prompt=bad), self.assertRaises(ValueError):
                config.prompt_text(REPO, bad)

    def test_followups_skip_comments(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "followups.txt"
            path.write_text("# comment\n\nfirst\n  # indented comment\nsecond  \n")
            self.assertEqual(config.load_followups(path), ["first", "second"])


class Goal(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "prompt-goal.toml"

    def load(self, text):
        self.path.write_text(text)
        return config.load_goal(self.path)

    def assert_error(self, text, *parts):
        with self.assertRaises(ValueError) as ctx:
            self.load(text)
        message = str(ctx.exception)
        self.assertTrue(message.startswith(f"{self.path}:"), message)
        for part in parts:
            self.assertIn(part, message)

    def test_both_checks(self):
        goal = self.load(
            '[[check]]\ntype = "pages_live"\nwait_s = 180\n\n[[check]]\ntype = "homepage_is_pages"\n'
        )
        self.assertEqual(goal, config.Goal(pages_live=True, homepage_is_pages=True, pages_wait_s=180))

    def test_homepage_check_alone_waits_for_no_build(self):
        goal = self.load('[[check]]\ntype = "homepage_is_pages"\n')
        self.assertEqual(goal, config.Goal(pages_live=False, homepage_is_pages=True, pages_wait_s=0))

    def test_pages_live_alone(self):
        goal = self.load('[[check]]\ntype = "pages_live"\nwait_s = 0\n')
        self.assertEqual(goal, config.Goal(pages_live=True, homepage_is_pages=False, pages_wait_s=0))

    def test_missing_file_is_an_error(self):
        with self.assertRaises(ValueError) as ctx:
            config.load_goal(self.path)
        self.assertIn("not found", str(ctx.exception))

    def test_invalid_toml_names_the_line(self):
        self.assert_error('[[check]]\ntype = \n', "line 2")

    def test_no_check_is_an_error(self):
        for text in ("", "check = []\n"):
            with self.subTest(text=text):
                self.assert_error(text, "at least one [[check]]")

    def test_unknown_top_level_key(self):
        self.assert_error('goal = "x"\n[[check]]\ntype = "homepage_is_pages"\n', "unknown key 'goal'")

    def test_unknown_type_lists_the_known_ones(self):
        self.assert_error('[[check]]\ntype = "app_works"\n', "check 1", "'app_works'", "pages_live")

    def test_missing_type(self):
        self.assert_error('[[check]]\nwait_s = 1\n', "check 1", "type")

    def test_type_that_is_not_a_string(self):
        for text in ('[[check]]\ntype = ["pages_live"]\n', "check = [1]\n"):
            with self.subTest(text=text):
                self.assert_error(text, "check 1", "type")

    def test_unknown_field(self):
        self.assert_error(
            '[[check]]\ntype = "homepage_is_pages"\nwait_s = 5\n', "check 1", "unknown key 'wait_s'"
        )

    def test_pages_live_needs_a_wait(self):
        for wait in ("", "wait_s = -1\n", "wait_s = 1.5\n", 'wait_s = "180"\n', "wait_s = true\n"):
            with self.subTest(wait=wait):
                self.assert_error(f'[[check]]\ntype = "pages_live"\n{wait}', "check 1", "wait_s")

    def test_duplicate_type(self):
        self.assert_error(
            '[[check]]\ntype = "homepage_is_pages"\n[[check]]\ntype = "homepage_is_pages"\n',
            "check 2", "duplicate",
        )

    def test_repo_goal_file_is_valid(self):
        # Only that it loads: which checks the scenario uses is up to the file.
        config.load_goal(REPO / config.GOAL_FILE)

    def test_describe(self):
        both = config.Goal(pages_live=True, homepage_is_pages=True, pages_wait_s=180)
        self.assertEqual(config.describe_goal(both), "pages_live (wait up to 180 s), homepage_is_pages")
        self.assertEqual(
            config.describe_goal(config.Goal(pages_live=False, homepage_is_pages=True)), "homepage_is_pages"
        )


class Assessment(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.path = self.root / "prompt-assessment.toml"
        (self.root / "prompt-assessment.js").write_text("window.labAssess = async () => ({});\n")

    def load(self, text):
        self.path.write_text(text)
        return config.load_assessment(self.path, self.root)

    def assert_error(self, text, *parts):
        with self.assertRaises(ValueError) as ctx:
            self.load(text)
        message = str(ctx.exception)
        self.assertTrue(message.startswith(f"{self.path}:"), message)
        for part in parts:
            self.assertIn(part, message)

    def test_missing_file_means_no_assessment(self):
        self.assertIsNone(config.load_assessment(self.path, self.root))

    def test_checks_in_file_order(self):
        text = '[[check]]\ntype = "reacts_to_clicks"\n[[check]]\ntype = "page_ok"\n[[check]]\ntype = "browser_script"\n'
        self.assertEqual(self.load(text).checks, ("reacts_to_clicks", "page_ok", "browser_script"))

    def test_browser_script_needs_the_script_file(self):
        (self.root / config.ASSESSMENT_SCRIPT).unlink()
        self.assert_error('[[check]]\ntype = "browser_script"\n', "check 1", "needs prompt-assessment.js in the lab root")
        self.assertEqual(self.load('[[check]]\ntype = "page_ok"\n').checks, ("page_ok",))

    def test_unknown_type_and_key(self):
        self.assert_error('[[check]]\ntype = "pages_live"\n', "check 1", "'pages_live'", "page_ok")
        self.assert_error('[[check]]\ntype = "browser_script"\nscript = "x.js"\n', "unknown key 'script'")

    def test_duplicate_type(self):
        self.assert_error('[[check]]\ntype = "page_ok"\n[[check]]\ntype = "page_ok"\n', "check 2", "duplicate")

    def test_invalid_or_empty_file(self):
        self.assert_error("[[check]]\ntype = \n", "line 2")
        self.assert_error("", "at least one [[check]]")

    def test_describe(self):
        assessment = config.Assessment(("page_ok", "browser_script"))
        self.assertEqual(config.describe_assessment(assessment), "page_ok, browser_script (prompt-assessment.js)")


class Profiles(unittest.TestCase):
    def test_profiles_share_permission_lists(self):
        settings = {p: config.profile_settings(REPO, p) for p in PROFILES}
        base = settings["clean"]["permissions"]["allow"]
        for name, data in settings.items():
            with self.subTest(profile=name):
                # The shared base list is identical; dev-stack and dev-messy append MCP rules.
                self.assertEqual(data["permissions"]["allow"][: len(base)], base)
                self.assertEqual(data["permissions"]["deny"], ["Bash(cd:*)"])
                self.assertEqual(set(data), {"enabledPlugins", "permissions"})
                self.assertEqual(set(data["permissions"]), {"allow", "deny"})
        for name in ("clean", "superpowers"):
            self.assertEqual(settings[name]["permissions"]["allow"], base)

    def test_dev_stack_extra_allow_rules_are_the_mcp_servers(self):
        base = config.profile_settings(REPO, "clean")["permissions"]["allow"]
        allow = config.profile_settings(REPO, "dev-stack")["permissions"]["allow"]
        self.assertEqual(
            allow[len(base):],
            [
                "mcp__plugin_playwright_playwright",
                "mcp__plugin_microsoft-docs_microsoft-learn",
            ],
        )

    def test_profile_plugins(self):
        market = "@claude-plugins-official"
        self.assertEqual(config.profile_settings(REPO, "clean")["enabledPlugins"], {})
        self.assertEqual(
            config.profile_settings(REPO, "superpowers")["enabledPlugins"],
            {"superpowers" + market: True},
        )
        self.assertEqual(
            config.profile_settings(REPO, "dev-stack")["enabledPlugins"],
            {n + market: True for n in ("superpowers", "frontend-design", "microsoft-docs", "playwright")},
        )

    def test_dev_messy_plugins(self):
        market = "@claude-plugins-official"
        skills = (
            "superpowers", "frontend-design", "code-review", "pr-review-toolkit", "code-simplifier",
            "feature-dev", "commit-commands", "claude-md-management", "skill-creator", "claude-code-setup",
        )  # fmt: skip
        # No chrome-devtools-mcp plugin: its browser profile in the home dir cannot be isolated (R46).
        mcp = ("playwright", "microsoft-docs")
        enabled = config.profile_settings(REPO, "dev-messy")["enabledPlugins"]
        self.assertEqual(list(enabled), [n + market for n in skills + mcp])
        self.assertTrue(all(v is True for v in enabled.values()))

    def test_dev_messy_mcp_servers_are_pinned(self):
        def npx(*args):
            return {"type": "stdio", "command": "npx", "args": ["-y", *args]}

        self.assertEqual(config.profile_mcp_servers(REPO, "dev-messy"), {
            "filesystem": npx("@modelcontextprotocol/server-filesystem@1.0.0", "{work}"),
            # In the config dir, which every run recreates: no memory carries over.
            "memory": npx("@modelcontextprotocol/server-memory@1.0.0")
            | {"env": {"MEMORY_FILE_PATH": "{config}/mcp-memory.jsonl"}},
            "sequential-thinking": npx("@modelcontextprotocol/server-sequential-thinking@1.0.0"),
            "everything": npx("@modelcontextprotocol/server-everything@1.0.0"),
            "context7": npx("@upstash/context7-mcp@4.4.1") | {"env": {"OTEL_SDK_DISABLED": "true"}},
            # --isolated: a fresh browser profile per server start (in memory for Playwright, a
            # temporary directory removed on close for chrome-devtools), nothing reused (R46).
            "chrome-devtools": npx("chrome-devtools-mcp@1.10.1", "--isolated"),
            "playwright": npx("@playwright/mcp@0.0.83", "--isolated"),
            "git": npx("@cyanheads/git-mcp-server@2.15.3"),
            "deepwiki": {"type": "http", "url": "https://mcp.deepwiki.com/mcp"},
        })  # fmt: skip

    def test_dev_messy_extra_allow_rules_are_its_mcp_servers(self):
        base = config.profile_settings(REPO, "clean")["permissions"]["allow"]
        allow = config.profile_settings(REPO, "dev-messy")["permissions"]["allow"]
        servers = config.profile_mcp_servers(REPO, "dev-messy")
        self.assertEqual(
            allow[len(base):],
            [
                "mcp__plugin_playwright_playwright",
                "mcp__plugin_microsoft-docs_microsoft-learn",
                *(f"mcp__{name}" for name in servers),
            ],
        )

    def test_dev_messy_excluded_servers(self):
        # desktop-commander: telemetry on by default, state in the home dir, unrestricted shell.
        # playwright-automation: writes ~/playwright-mcp-server.log in stdio mode.
        # microsoft-learn: same URL as the microsoft-docs plugin's server, which Claude Code then drops.
        settings = config.profile_settings(REPO, "dev-messy")
        servers = config.profile_mcp_servers(REPO, "dev-messy")
        allow = settings["permissions"]["allow"]
        for name in ("desktop-commander", "playwright-automation", "microsoft-learn"):
            with self.subTest(server=name):
                self.assertNotIn(name, servers)
                self.assertNotIn(f"mcp__{name}", allow)
        self.assertEqual(len(servers), 9)
        self.assertIn("mcp__plugin_microsoft-docs_microsoft-learn", allow)  # the plugin's server stays

    def test_other_profiles_have_no_mcp_file(self):
        for name in ("clean", "superpowers", "dev-stack"):
            with self.subTest(profile=name):
                self.assertEqual(config.profile_mcp_servers(REPO, name), {})

    def test_profile_settings_unknown_rejected(self):
        with self.assertRaises(ValueError):
            config.profile_settings(REPO, "nope")

    def test_profile_settings_rejects_path_traversal(self):
        for bad in ("../prompts", "..", "."):
            with self.subTest(profile=bad), self.assertRaises(ValueError):
                config.profile_settings(REPO, bad)


class ProfileMcpServers(TempLab):
    """lab/profiles/<p>/mcp.json: user-scope MCP servers of a profile."""

    def write(self, text, profile="clean"):
        (self.lab_root / "lab" / "profiles" / profile / "mcp.json").write_text(text)

    def test_missing_file_gives_no_servers(self):
        self.assertEqual(config.profile_mcp_servers(self.lab_root, "clean"), {})

    def test_servers_are_read(self):
        servers = {"memory": {"type": "stdio", "command": "npx", "args": ["-y", "pkg@1.0.0"]}}
        self.write(json.dumps({"mcpServers": servers}))
        self.assertEqual(config.profile_mcp_servers(self.lab_root, "clean"), servers)

    def test_invalid_file_names_the_file(self):
        for text in ("{not json", "[]", '{"servers": {}}', '{"mcpServers": []}', '{"mcpServers": {"a": "npx"}}'):
            with self.subTest(text=text):
                self.write(text)
                with self.assertRaises(ValueError) as ctx:
                    config.profile_mcp_servers(self.lab_root, "clean")
                self.assertIn(str(self.lab_root / "lab" / "profiles" / "clean" / "mcp.json"), str(ctx.exception))

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            config.profile_mcp_servers(self.lab_root, "nope")


class CliWiring(TempLab):
    @staticmethod
    def load_lab_module():
        # `lab` is also the name of the package directory, so load lab.py by path.
        spec = importlib.util.spec_from_file_location("lab_cli", REPO / "lab.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def uses_mcp(self, text):
        cli = self.load_lab_module()
        self.matrix.write_text(text)
        with mock.patch.object(cli, "MATRIX_FILE", self.matrix):
            return cli.matrix_uses_mcp()

    def test_mcp_only_with_an_mcp_profile_in_matrix(self):
        self.assertFalse(self.uses_mcp("test1 clean prompt-simple.md\ntest2 superpowers prompt-simple.md\n"))
        self.assertTrue(self.uses_mcp("test1 clean prompt-simple.md\ntest2 dev-stack prompt-simple.md\n"))
        self.assertTrue(self.uses_mcp("test1 clean prompt-simple.md\ntest2 dev-messy prompt-simple.md\n"))

    def test_repo_matrix_uses_mcp(self):
        self.assertTrue(self.load_lab_module().matrix_uses_mcp())

    def test_run_and_session_stop_on_a_missing_goal_file(self):
        cli = self.load_lab_module()
        cfg = {"LAB_GH_ORG": "org", "GH_TOKEN": "ghp_x"}
        for name, call in (("run", lambda: cli.run_tests(cfg, "all", 1)),
                           ("session", lambda: cli.run_session(cfg, "test1", True))):
            err = io.StringIO()
            with self.subTest(command=name), \
                 mock.patch.object(cli, "GOAL_FILE", self.lab_root / "prompt-goal.toml"), \
                 mock.patch.object(cli.github, "GitHub") as gh, \
                 contextlib.redirect_stderr(err):
                self.assertEqual(call(), 1)
                gh.assert_not_called()
                self.assertIn("prompt-goal.toml: not found", err.getvalue())

    def test_an_assessment_file_makes_prepare_require_chrome(self):
        cli = self.load_lab_module()
        self.matrix.write_text("test1 clean prompt-simple.md\n")
        assessment = self.lab_root / "prompt-assessment.toml"
        for exists in (False, True):
            if exists:
                assessment.write_text('[[check]]\ntype = "page_ok"\n')
            with self.subTest(exists=exists), \
                 mock.patch.object(cli, "MATRIX_FILE", self.matrix), \
                 mock.patch.object(cli, "ASSESSMENT_FILE", assessment), \
                 mock.patch.object(cli, "prepare", return_value={}) as prepare, \
                 mock.patch.object(cli, "run_setup", return_value=0), \
                 mock.patch.object(sys, "argv", ["lab.py", "setup"]):
                self.assertEqual(cli.main(), 0)
                self.assertEqual(prepare.call_args.kwargs["need_assessment"], exists)

    def test_tokens_command_ignores_broken_matrix(self):
        cli = self.load_lab_module()
        self.matrix.write_text("broken\n")
        with mock.patch.object(cli, "MATRIX_FILE", self.matrix), \
             mock.patch.object(sys, "argv", ["lab.py", "tokens", "github"]), \
             mock.patch.object(tokens, "create_github_token", return_value=0):
            self.assertEqual(cli.main(), 0)


if __name__ == "__main__":
    unittest.main()
