import argparse
import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labkit import env, preflight, tokens  # noqa: E402

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
TOKEN = "sk-ant-oat01-SECRETSECRET"


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.dir.name).resolve()

    def tearDown(self):
        self.dir.cleanup()


class FileProblems(TempDirCase):
    def test_file_problems_mode(self):
        path = self.tmp / ".env"
        path.write_text("A=1\n")
        path.chmod(0o644)
        problems = env.file_problems(path, self.tmp)
        self.assertEqual(len(problems), 1)
        self.assertIn("0o644", problems[0])
        self.assertIn("0o600", problems[0])
        self.assertIn("fix with `chmod 600 .env`", problems[0])

    def test_file_problems_ok(self):
        path = self.tmp / ".env"
        path.write_text("A=1\n")
        path.chmod(0o600)
        self.assertEqual(env.file_problems(path, self.tmp), [])

    def test_file_problems_missing_file(self):
        self.assertEqual(env.file_problems(self.tmp / ".env", self.tmp), [])

    def test_file_problems_tracked(self):
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        path = self.tmp / ".env"
        path.write_text("A=1\n")
        path.chmod(0o600)
        subprocess.run(["git", "add", "-f", ".env"], cwd=self.tmp, check=True)
        self.assertEqual(
            env.file_problems(path, self.tmp),
            ["tracked by git: remove it from git with `git rm --cached .env`"],
        )

    def test_file_problems_untracked_in_repo(self):
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        path = self.tmp / ".env"
        path.write_text("A=1\n")
        path.chmod(0o600)
        self.assertEqual(env.file_problems(path, self.tmp), [])


class Check(TempDirCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.sandbox = self.tmp / "a" / "b" / "sandbox"
        self.home.mkdir()
        self.sandbox.mkdir(parents=True)
        which = mock.patch.object(preflight.shutil, "which", lambda name: f"/usr/bin/{name}")
        which.start()
        self.addCleanup(which.stop)

    def run_check(self, cfg=None, **kwargs):
        full = {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}
        full.update(cfg or {})
        opts = dict(need_github=False, need_node=False, need_chrome=False, timeout_s=1200, now=NOW,
                    sandbox=self.sandbox, home=self.home)
        opts.update(kwargs)
        return preflight.check(full, **opts)

    def test_all_good(self):
        self.assertEqual(self.run_check(), ([], []))

    def test_missing_claude_token(self):
        errors, _ = preflight.check(
            {}, need_github=False, need_node=False, need_chrome=False, timeout_s=1200, now=NOW,
            sandbox=self.sandbox, home=self.home)
        self.assertEqual(len(errors), 1)
        self.assertIn("python3 lab.py tokens claude", errors[0])

    def test_missing_tools(self):
        with mock.patch.object(preflight.shutil, "which", lambda name: None):
            errors, _ = self.run_check(need_node=True)
        for tool in ("claude", "git", "gh", "npx"):
            self.assertTrue(any(f"`{tool}`" in e for e in errors), tool)

    def test_npx_only_needed_with_node(self):
        with mock.patch.object(preflight.shutil, "which", lambda name: None if name == "npx" else "/x"):
            self.assertEqual(self.run_check(need_node=False), ([], []))
            errors, _ = self.run_check(need_node=True)
        self.assertEqual(len(errors), 1)
        self.assertIn("npx", errors[0])

    def test_chrome_only_needed_with_need_chrome(self):
        with mock.patch.object(preflight, "chrome_installed", return_value=False):
            self.assertEqual(self.run_check(need_chrome=False), ([], []))
            errors, warnings = self.run_check(need_chrome=True)
        self.assertEqual(len(errors), 1)
        self.assertIn("Google Chrome", errors[0])
        self.assertIn("lab/matrix.conf", errors[0])  # says why the check applies
        self.assertEqual(warnings, [])
        with mock.patch.object(preflight, "chrome_installed", return_value=True):
            self.assertEqual(self.run_check(need_chrome=True), ([], []))

    def test_old_python(self):
        with mock.patch.object(preflight.sys, "version_info", (3, 10, 0)):
            errors, _ = self.run_check()
        self.assertEqual(len(errors), 1)
        self.assertIn("3.11", errors[0])

    def test_github_settings_only_needed_when_requested(self):
        self.assertEqual(self.run_check(need_github=False), ([], []))
        errors, _ = self.run_check(need_github=True)
        self.assertTrue(any("LAB_GH_ORG" in e for e in errors))
        self.assertTrue(any("GH_TOKEN" in e and "python3 lab.py tokens github" in e for e in errors))

    def test_github_token_expires_before_timeout(self):
        cfg = {"LAB_GH_ORG": "org", "GH_TOKEN": "ghp_x",
               "GH_TOKEN_EXPIRES_AT": (NOW + timedelta(minutes=10)).isoformat()}
        errors, _ = self.run_check(cfg, need_github=True, timeout_s=1200)
        self.assertEqual(len(errors), 1)
        self.assertIn("python3 lab.py tokens github", errors[0])

    def test_github_token_valid_long_enough(self):
        cfg = {"LAB_GH_ORG": "org", "GH_TOKEN": "ghp_x",
               "GH_TOKEN_EXPIRES_AT": (NOW + timedelta(hours=2)).isoformat()}
        self.assertEqual(self.run_check(cfg, need_github=True), ([], []))

    def test_github_expiry_with_microseconds_as_stored(self):
        cfg = {"LAB_GH_ORG": "org", "GH_TOKEN": "ghp_x",
               "GH_TOKEN_EXPIRES_AT": "2026-10-13T04:33:25.367909+00:00"}
        self.assertEqual(self.run_check(cfg, need_github=True), ([], []))

    def test_unreadable_expiry_is_error(self):
        cfg = {"LAB_GH_ORG": "org", "GH_TOKEN": "ghp_x", "GH_TOKEN_EXPIRES_AT": "tomorrow"}
        errors, _ = self.run_check(cfg, need_github=True)
        self.assertEqual(len(errors), 1)
        self.assertIn("GH_TOKEN_EXPIRES_AT", errors[0])
        self.assertIn("python3 lab.py tokens github", errors[0])

    def test_missing_expiry_is_warning(self):
        cfg = {"LAB_GH_ORG": "org", "GH_TOKEN": "ghp_x"}
        errors, warnings = self.run_check(cfg, need_github=True)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("GH_TOKEN_EXPIRES_AT", warnings[0])

    def test_parent_claude_md_warns(self):
        (self.tmp / "a" / "CLAUDE.md").write_text("x")
        errors, warnings = self.run_check()
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("a/CLAUDE.md", warnings[0])

    def test_parent_claude_local_md_warns(self):
        (self.tmp / "a" / "b" / "CLAUDE.local.md").write_text("x")
        _, warnings = self.run_check()
        self.assertEqual(len(warnings), 1)
        self.assertIn("b/CLAUDE.local.md", warnings[0])

    def test_parent_dot_claude_claude_md_warns(self):
        (self.tmp / "a" / ".claude").mkdir()
        (self.tmp / "a" / ".claude" / "CLAUDE.md").write_text("x")
        _, warnings = self.run_check()
        self.assertEqual(len(warnings), 1)
        self.assertIn("a/.claude/CLAUDE.md", warnings[0])

    def test_warning_names_memory_not_settings(self):
        (self.tmp / "a" / "CLAUDE.md").write_text("x")
        _, warnings = self.run_check()
        self.assertIn("may be loaded by Claude Code as memory, which skews token counts", warnings[0])

    def test_parent_dot_claude_with_only_settings_does_not_warn(self):
        # Claude Code reads settings only from the project directory, not from parents.
        (self.tmp / "a" / ".claude").mkdir()
        (self.tmp / "a" / ".claude" / "settings.local.json").write_text("{}")
        (self.tmp / "a" / ".claude" / "settings.json").write_text("{}")
        self.assertEqual(self.run_check(), ([], []))

    def test_empty_parent_dot_claude_does_not_warn(self):
        (self.tmp / "a" / ".claude").mkdir()
        self.assertEqual(self.run_check(), ([], []))

    def test_sandbox_itself_is_not_a_parent(self):
        (self.sandbox / "CLAUDE.md").write_text("x")
        (self.sandbox / ".claude").mkdir()
        (self.sandbox / ".claude" / "CLAUDE.md").write_text("x")
        self.assertEqual(self.run_check(), ([], []))

    def test_home_claude_md_ignored(self):
        sandbox = self.home / "lab" / "sandbox"
        sandbox.mkdir(parents=True)
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "CLAUDE.md").write_text("x")
        self.assertEqual(self.run_check(sandbox=sandbox), ([], []))

    def test_other_memory_file_in_home_still_warns(self):
        sandbox = self.home / "lab" / "sandbox"
        sandbox.mkdir(parents=True)
        (self.home / "lab" / ".claude").mkdir()
        (self.home / "lab" / ".claude" / "CLAUDE.md").write_text("x")
        _, warnings = self.run_check(sandbox=sandbox)
        self.assertEqual(len(warnings), 1)

    def test_messages_never_contain_token(self):
        (self.tmp / "a" / "CLAUDE.md").write_text("x")
        cfg = {"GH_TOKEN": "ghp_SECRETSECRET", "GH_TOKEN_EXPIRES_AT": "garbage"}
        with mock.patch.object(preflight.shutil, "which", lambda name: None):
            errors, warnings = self.run_check(cfg, need_github=True, need_node=True)
        self.assertTrue(errors and warnings)
        for message in errors + warnings:
            self.assertNotIn("SECRETSECRET", message)
        errors, warnings = self.run_check({"GH_TOKEN": "ghp_SECRETSECRET"}, need_github=True)
        for message in errors + warnings:
            self.assertNotIn("SECRETSECRET", message)


class ChromeInstalled(TempDirCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.platform = mock.patch.object(preflight.sys, "platform", "darwin")
        self.platform.start()
        self.addCleanup(self.platform.stop)

    @staticmethod
    def nothing_on_path(name):
        return None

    def test_found_in_system_applications_on_macos(self):
        app = self.tmp / "Applications" / "Google Chrome.app"
        app.mkdir(parents=True)
        with mock.patch.object(preflight, "MACOS_APP_DIRS", (self.tmp / "Applications",)):
            self.assertTrue(preflight.chrome_installed(self.home, which=self.nothing_on_path))

    def test_found_in_users_applications_on_macos(self):
        (self.home / "Applications" / "Google Chrome.app").mkdir(parents=True)
        with mock.patch.object(preflight, "MACOS_APP_DIRS", (self.tmp / "none",)):
            self.assertTrue(preflight.chrome_installed(self.home, which=self.nothing_on_path))

    def test_found_on_path_elsewhere(self):
        with mock.patch.object(preflight.sys, "platform", "linux"):
            for name in ("google-chrome", "google-chrome-stable", "chrome"):
                self.assertTrue(
                    preflight.chrome_installed(self.home, which=lambda n, want=name: f"/usr/bin/{n}" if n == want else None),
                    name,
                )

    def test_not_found(self):
        with mock.patch.object(preflight, "MACOS_APP_DIRS", (self.tmp / "none",)):
            self.assertFalse(preflight.chrome_installed(self.home, which=self.nothing_on_path))
        with mock.patch.object(preflight.sys, "platform", "linux"):
            self.assertFalse(preflight.chrome_installed(self.home, which=self.nothing_on_path))

    def test_macos_app_is_not_looked_up_on_other_systems(self):
        (self.home / "Applications" / "Google Chrome.app").mkdir(parents=True)
        with mock.patch.object(preflight.sys, "platform", "linux"):
            self.assertFalse(preflight.chrome_installed(self.home, which=self.nothing_on_path))


class Prepare(unittest.TestCase):
    @staticmethod
    def load_lab_module():
        # `lab` is also the name of the package directory, so load lab.py by path.
        path = Path(__file__).resolve().parents[2] / "lab.py"
        spec = importlib.util.spec_from_file_location("lab_cli", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.env_file = self.tmp / ".env"  # never the developer's real .env

    def prepare(self, command, errors, warnings, file_problems=(), need_mcp=False, args=None,
                results_file=None, environ=None):
        cli = self.load_lab_module()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "ENV_FILE", self.env_file), \
             mock.patch.object(cli, "results_file", return_value=results_file or self.tmp / "none.csv"), \
             mock.patch.object(preflight, "check", return_value=(errors, warnings)) as check, \
             mock.patch.object(env, "file_problems", return_value=list(file_problems)), \
             mock.patch.dict("os.environ", {"GH_TOKEN": "ghp_SECRETSECRET", **(environ or {})}), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cfg = cli.prepare(command, args or argparse.Namespace(), need_mcp=need_mcp)
                code = None
            except SystemExit as e:
                cfg, code = None, e.code
        return cfg, code, out.getvalue() + err.getvalue(), check

    def old_results(self) -> Path:
        """A results.csv written by an older lab version (other columns)."""
        path = self.tmp / "results.csv"
        path.write_text("timestamp,test_id\n2026-01-01,test1\n")
        return path

    def test_old_results_file_stops_run_and_session_that_record_a_row(self):
        path = self.old_results()
        for command, args in (
            ("run", argparse.Namespace()),
            ("session", argparse.Namespace(no_prompt=False)),
        ):
            _, code, output, _ = self.prepare(command, [], [], args=args, results_file=path)
            self.assertEqual(code, 1, command)
            self.assertIn("other columns", output, command)

    def test_old_results_file_does_not_stop_session_no_prompt(self):
        # No row is recorded, so the old file is not in the way.
        _, code, output, _ = self.prepare(
            "session", [], [], args=argparse.Namespace(no_prompt=True), results_file=self.old_results()
        )
        self.assertIsNone(code, output)
        self.assertNotIn("other columns", output)

    def test_prepare_reads_the_env_file_it_is_given(self):
        self.env_file.write_text("LAB_GH_ORG=from-temp-file\n")
        self.env_file.chmod(0o600)
        cfg, code, _, _ = self.prepare("run", [], [])
        self.assertIsNone(code)
        self.assertEqual(cfg["LAB_GH_ORG"], "from-temp-file")

    def test_errors_exit_with_status_1_and_print_them(self):
        cfg, code, output, _ = self.prepare("run", ["no claude"], [])
        self.assertEqual(code, 1)
        self.assertIn("no claude", output)

    def test_file_problems_are_errors(self):
        _, code, output, _ = self.prepare("run", [], [], file_problems=["tracked by git"])
        self.assertEqual(code, 1)
        self.assertIn(".env", output)
        self.assertIn("tracked by git", output)

    def test_warnings_only_are_printed_and_cfg_returned(self):
        cfg, code, output, _ = self.prepare("run", [], ["parent CLAUDE.md"])
        self.assertIsNone(code)
        self.assertIn("parent CLAUDE.md", output)
        self.assertEqual(cfg["GH_TOKEN"], "ghp_SECRETSECRET")

    def test_need_github_mapping_and_need_mcp_pass_through(self):
        for command in ("setup", "run", "session"):
            *_, check = self.prepare(command, [], [], need_mcp=True)
            self.assertTrue(check.call_args.kwargs["need_github"], command)
            self.assertTrue(check.call_args.kwargs["need_node"], command)
            self.assertTrue(check.call_args.kwargs["need_chrome"], command)
            self.assertTrue(check.call_args.kwargs["need_claude_token"], command)
            self.assertEqual(check.call_args.kwargs["timeout_s"], 1200)
        *_, check = self.prepare("report", [], [])
        self.assertFalse(check.call_args.kwargs["need_github"])
        self.assertFalse(check.call_args.kwargs["need_node"])
        self.assertFalse(check.call_args.kwargs["need_chrome"])

    def test_teardown_needs_no_claude_token_npx_chrome_or_github_margin(self):
        *_, check = self.prepare("teardown", [], [], need_mcp=True)
        kwargs = check.call_args.kwargs
        self.assertEqual(
            (kwargs["need_github"], kwargs["need_node"], kwargs["need_chrome"], kwargs["need_claude_token"]),
            (False, False, False, False),
        )

    def test_run_timeout_from_the_environment_reaches_the_token_check(self):
        *_, check = self.prepare("run", [], [], environ={"LAB_RUN_TIMEOUT_S": "3600"})
        self.assertEqual(check.call_args.kwargs["timeout_s"], 3600)

    def test_bad_settings_are_errors(self):
        for key, value in (("LAB_RUN_TIMEOUT_S", "0"), ("LAB_RUN_TIMEOUT_S", "20m"),
                           ("LAB_LIMIT_MIN_PCT", "-1"), ("LAB_LIMIT_MIN_PCT", "101"),
                           ("LAB_LIMIT_MIN_PCT", "ten")):
            with self.subTest(key=key, value=value):
                _, code, output, _ = self.prepare("run", [], [], environ={key: value})
                self.assertEqual(code, 1)
                self.assertIn(key, output)

    def test_limit_min_pct_takes_0_to_100(self):
        for value in ("0", "100"):
            with self.subTest(value=value):
                _, code, output, _ = self.prepare("run", [], [], environ={"LAB_LIMIT_MIN_PCT": value})
                self.assertIsNone(code, output)

    def test_teardown_ignores_bad_settings(self):
        _, code, output, _ = self.prepare(
            "teardown", [], [], environ={"LAB_RUN_TIMEOUT_S": "x", "LAB_LIMIT_MIN_PCT": "x"}
        )
        self.assertIsNone(code, output)

    def real_prepare(self, command, environ):
        """prepare with the real checks, tools found on PATH and only `environ` set."""
        cli = self.load_lab_module()
        out = io.StringIO()
        with mock.patch.object(cli, "ENV_FILE", self.env_file), \
             mock.patch.object(preflight.shutil, "which", lambda name: f"/usr/bin/{name}"), \
             mock.patch.dict("os.environ", environ, clear=True), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                cli.prepare(command, argparse.Namespace(), need_mcp=True)
                return None, out.getvalue()
            except SystemExit as e:
                return e.code, out.getvalue()

    def test_teardown_rerun_without_claude_token_passes_preflight(self):
        # After a failed repo delete the Claude token is gone, the GitHub token stays.
        code, output = self.real_prepare("teardown", {
            "LAB_GH_ORG": "org", "GH_TOKEN": "ghp_x",
            "GH_TOKEN_EXPIRES_AT": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        })  # fmt: skip
        self.assertIsNone(code, output)
        self.assertEqual(self.real_prepare("run", {"LAB_GH_ORG": "org", "GH_TOKEN": "ghp_x"})[0], 1)

    def test_teardown_with_expired_github_token_passes_preflight(self):
        code, output = self.real_prepare("teardown", {
            "CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "LAB_GH_ORG": "org", "GH_TOKEN": "ghp_x",
            "GH_TOKEN_EXPIRES_AT": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        })  # fmt: skip
        self.assertIsNone(code, output)

    def test_help_lists_the_first_run_in_order(self):
        cli = self.load_lab_module()
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["lab.py", "--help"]), contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()
        self.assertEqual(ctx.exception.code, 0)
        text = out.getvalue()
        steps = [
            "organization", "python3 lab.py tokens claude", "LAB_GH_ORG",
            "python3 lab.py tokens github ", "python3 lab.py tokens github --check",
            "python3 lab.py setup", "python3 lab.py run test1", "python3 lab.py report",
            "python3 lab.py teardown --secrets",
        ]  # fmt: skip
        positions = [text.find(step) for step in steps]
        self.assertNotIn(-1, positions, dict(zip(steps, positions)))
        self.assertEqual(positions, sorted(positions))
        for command in ("session", "--repeat", "--no-prompt", "--yes"):
            self.assertIn(command, text)

    def test_tokens_command_skips_prepare(self):
        cli = self.load_lab_module()
        with mock.patch.object(sys, "argv", ["lab.py", "tokens", "github"]), \
             mock.patch.object(tokens, "create_github_token", return_value=0), \
             mock.patch.object(cli, "prepare") as prepare:
            self.assertEqual(cli.main(), 0)
        prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
