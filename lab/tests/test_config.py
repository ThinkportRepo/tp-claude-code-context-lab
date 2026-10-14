import importlib.util
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
    """A throwaway lab directory with lab/profiles/<name>/ directories."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.lab_root = Path(self.dir.name).resolve()
        for name in PROFILES:
            (self.lab_root / "lab" / "profiles" / name).mkdir(parents=True)
        self.matrix = self.lab_root / "lab" / "matrix.conf"

    def tearDown(self):
        self.dir.cleanup()

    def load(self, text, environ=None):
        self.matrix.write_text(text)
        return config.load_matrix(self.matrix, environ or {})


class Matrix(TempLab):
    def test_matrix_model_precedence(self):
        text = "test1 clean simple m-col\ntest2 clean well-defined\n"
        tests = self.load(text, {"LAB_MODEL": "m-env"})
        self.assertEqual([t.model for t in tests], ["m-col", "m-env"])
        tests = self.load(text, {})
        self.assertEqual([t.model for t in tests], ["m-col", config.DEFAULT_MODEL])
        tests = self.load(text, {"LAB_MODEL": ""})
        self.assertEqual(tests[1].model, config.DEFAULT_MODEL)
        self.assertEqual(config.DEFAULT_MODEL, "claude-haiku-5-5")

    def test_matrix_parses_columns_and_skips_comments(self):
        tests = self.load("# id profile prompt\n\n  test1\tclean   simple\n")
        self.assertEqual(tests, [config.Test("test1", "clean", "simple", config.DEFAULT_MODEL)])

    def test_matrix_error_names_line(self):
        with self.assertRaises(ValueError) as ctx:
            self.load("# header\ntest1 clean simple\ntest2 clean\n")
        self.assertIn("matrix.conf:3", str(ctx.exception))
        self.assertIn(str(self.matrix), str(ctx.exception))

    def test_too_many_columns_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.load("test1 clean simple model high extra\n")
        self.assertIn("matrix.conf:1", str(ctx.exception))
        self.assertIn("[effort]", str(ctx.exception))

    def test_effort_column(self):
        text = (
            "test1 clean simple\n"
            "test2 clean simple m-col high\n"
            "test3 clean simple - medium\n"
            "test4 clean simple -\n"
        )
        tests = self.load(text, {"LAB_MODEL": "m-env"})
        self.assertEqual(
            [(t.model, t.effort) for t in tests],
            [("m-env", None), ("m-col", "high"), ("m-env", "medium"), ("m-env", None)],
        )
        self.assertEqual(self.load("test1 clean simple - low\n")[0].model, config.DEFAULT_MODEL)

    def test_every_effort_level_parses(self):
        for level in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=level):
                self.assertEqual(self.load(f"test1 clean simple - {level}\n")[0].effort, level)

    def test_invalid_effort_rejected(self):
        for bad in ("bogus", "Medium", "-"):
            with self.subTest(effort=bad):
                with self.assertRaises(ValueError) as ctx:
                    self.load(f"# header\ntest1 clean simple m {bad}\n")
                self.assertIn(f"{self.matrix}:2", str(ctx.exception))
                self.assertIn("effort", str(ctx.exception))

    def test_effort_defaults_to_none(self):
        self.assertIsNone(config.Test("test1", "clean", "simple", "m").effort)

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.load("test1 clean simple\ntest2 nope simple\n")
        self.assertIn("matrix.conf:2", str(ctx.exception))
        self.assertIn("nope", str(ctx.exception))

    def test_unknown_prompt_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.load("test1 clean poem\n")
        self.assertIn("matrix.conf:1", str(ctx.exception))
        self.assertIn("poem", str(ctx.exception))

    def test_duplicate_id_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.load("test1 clean simple\ntest1 superpowers simple\n")
        self.assertIn("matrix.conf:2", str(ctx.exception))
        self.assertIn("test1", str(ctx.exception))

    def test_test_ids_must_be_lab_test_names(self):
        # Each id becomes sandbox/<id>/ (wiped by every run) and repo lab-<id>.
        for bad in ("/tmp/victim", "../..", "..", "t1", "test1/../x", "test/x", "Test1", "test.1"):
            with self.subTest(id=bad):
                with self.assertRaises(ValueError) as ctx:
                    self.load(f"{bad} clean simple\n")
                self.assertIn("matrix.conf:1", str(ctx.exception))
        ids = [t.id for t in self.load("test clean simple\ntest_a-B9 clean simple\n")]
        self.assertEqual(ids, ["test", "test_a-B9"])

    def test_profile_names_must_be_plain(self):
        (self.lab_root / "lab" / "settings.json").write_text("{}")  # what `..` would reach
        for bad in ("..", ".", "Clean", "-x", "clean/../clean"):
            with self.subTest(profile=bad), self.assertRaises(ValueError):
                self.load(f"test1 {bad} simple\n")

    def test_repo_matrix_loads(self):
        tests = config.load_matrix(REPO / "lab" / "matrix.conf", {})
        default = config.DEFAULT_MODEL
        self.assertEqual(
            [(t.id, t.profile, t.prompt, t.model, t.effort) for t in tests],
            [
                ("test1", "clean", "simple", default, None),
                ("test2", "clean", "well-defined", default, None),
                ("test3", "superpowers", "simple", default, None),
                ("test4", "superpowers", "well-defined", default, None),
                ("test5", "dev-stack", "simple", default, None),
                ("test6", "dev-stack", "well-defined", default, None),
                ("test7", "dev-messy", "simple", default, None),
                ("test8", "dev-messy", "well-defined", default, None),
                ("test9", "clean", "simple", "claude-sonnet-5", "medium"),
            ],
        )


class Prompts(unittest.TestCase):
    def test_prompt_text_simple_is_lab_prompt_file(self):
        expected = (REPO / "lab" / "prompts" / "simple.txt").read_text()
        self.assertEqual(config.prompt_text(REPO, "simple"), expected)

    def test_simple_prompt_text_is_pinned(self):
        self.assertEqual(
            (REPO / "lab" / "prompts" / "simple.txt").read_text(),
            'Create a simple "Hello GitHub Pages" app using the Git repository located in the `xyz` '
            "directory. Publish it with GitHub Pages and link the site in the repository's About "
            "section.\n",
        )

    def test_prompt_text_well_defined_is_lab_root_file(self):
        expected = (REPO / "well-defined-prompt").read_text()
        self.assertEqual(config.prompt_text(REPO, "well-defined"), expected)

    def test_every_prompt_asks_for_the_about_link(self):
        # The lab's goal includes the repo's About -> Website field, so each prompt must ask for it.
        for name in ("simple", "well-defined"):
            with self.subTest(prompt=name):
                self.assertIn("About section", config.prompt_text(REPO, name))

    def test_prompt_text_unknown_rejected(self):
        with self.assertRaises(ValueError):
            config.prompt_text(REPO, "poem")

    def test_followups_skip_comments(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "followups.txt"
            path.write_text("# comment\n\nfirst\n  # indented comment\nsecond  \n")
            self.assertEqual(config.load_followups(path), ["first", "second"])

    def test_repo_followups(self):
        lines = config.load_followups(REPO / "lab" / "prompts" / "followups.txt")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("Go with your recommendation"))


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
        mcp = ("playwright", "microsoft-docs", "chrome-devtools-mcp")
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
            "chrome-devtools": npx("chrome-devtools-mcp@1.10.1"),
            "playwright": npx("@playwright/mcp@0.0.83"),
            "playwright-automation": npx("@executeautomation/playwright-mcp-server@1.0.12"),
            "git": npx("@cyanheads/git-mcp-server@2.15.3"),
            "deepwiki": {"type": "http", "url": "https://mcp.deepwiki.com/mcp"},
            "microsoft-learn": {"type": "http", "url": "https://learn.microsoft.com/api/mcp"},
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
                "mcp__plugin_chrome-devtools-mcp_chrome-devtools",
                *(f"mcp__{name}" for name in servers),
            ],
        )

    def test_dev_messy_excludes_desktop_commander(self):
        # Telemetry on by default, state in the home dir, unrestricted shell.
        settings = config.profile_settings(REPO, "dev-messy")
        self.assertNotIn("desktop-commander", config.profile_mcp_servers(REPO, "dev-messy"))
        self.assertFalse([r for r in settings["permissions"]["allow"] if "desktop-commander" in r])

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

    def needs_node(self, text):
        cli = self.load_lab_module()
        self.matrix.write_text(text)
        with mock.patch.object(cli, "MATRIX_FILE", self.matrix):
            return cli.matrix_needs_node()

    def test_need_node_only_with_an_mcp_profile_in_matrix(self):
        self.assertFalse(self.needs_node("test1 clean simple\ntest2 superpowers simple\n"))
        self.assertTrue(self.needs_node("test1 clean simple\ntest2 dev-stack simple\n"))
        self.assertTrue(self.needs_node("test1 clean simple\ntest2 dev-messy simple\n"))

    def test_repo_matrix_needs_node(self):
        self.assertTrue(self.load_lab_module().matrix_needs_node())

    def test_tokens_command_ignores_broken_matrix(self):
        cli = self.load_lab_module()
        self.matrix.write_text("broken\n")
        with mock.patch.object(cli, "MATRIX_FILE", self.matrix), \
             mock.patch.object(sys, "argv", ["lab.py", "tokens", "github"]), \
             mock.patch.object(tokens, "create_github_token", return_value=0):
            self.assertEqual(cli.main(), 0)


if __name__ == "__main__":
    unittest.main()
