import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labkit import config, env, teardown  # noqa: E402
from labkit.github import GitHubError  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
GH_TOKEN = "github_pat_LEAKCHECK0123456789"
OAUTH_TOKEN = "sk-ant-oat01-LEAKCHECK0123456789"
REVOKE_URL = "https://api.github.com/credentials/revoke"
USER_URL = "https://api.github.com/user"
GITHUB_TOKENS_URL = "https://github.com/settings/personal-access-tokens"
CLAUDE_URL = "https://claude.ai/settings/claude-code"
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
REVOKED_STDERR = "API Error: 401 OAuth token has been revoked\n"


class FakeGitHub:
    """Stand-in for labkit.github.GitHub; records deletions."""

    def __init__(self, repos=(), delete_fails=()):
        self.repos = list(repos)
        self.delete_fails = set(delete_fails)
        self.calls = []

    def list_repos(self, prefix):
        self.calls.append(("list_repos", prefix))
        return list(self.repos)

    def delete_repo(self, name):
        self.calls.append(("delete_repo", name))
        if name in self.delete_fails:
            raise GitHubError(f"delete repo {name}: boom")

    def deleted(self):
        return [c[1] for c in self.calls if c[0] == "delete_repo"]


class Plan(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sandbox = Path(tmp.name) / "sandbox"

    def test_plan_only_lab_repos(self):
        gh = FakeGitHub(["lab-test1", "other", "lab-s5check"])
        repos, _ = teardown.plan(gh, self.sandbox)
        self.assertEqual(repos, ["lab-test1", "lab-s5check"])

    def test_plan_excludes_look_alike_repos(self):
        gh = FakeGitHub(["lab-tools", "lab-s5check2", "lab-test10"])
        repos, _ = teardown.plan(gh, self.sandbox)
        self.assertEqual(repos, ["lab-test10"])

    def test_plan_dirs_are_the_directories_under_sandbox(self):
        for name in ("test1", "test2", ".gh"):
            (self.sandbox / name).mkdir(parents=True)
        (self.sandbox / "stray-file").write_text("x")
        (self.sandbox / "link").symlink_to(self.sandbox / "test1")
        _, dirs = teardown.plan(FakeGitHub(), self.sandbox)
        self.assertEqual(dirs, [self.sandbox / ".gh", self.sandbox / "test1", self.sandbox / "test2"])

    def test_plan_without_sandbox_has_no_dirs(self):
        self.assertEqual(teardown.plan(FakeGitHub(), self.sandbox), ([], []))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lab_root = Path(tmp.name)
        self.sandbox = self.lab_root / "sandbox"
        for name in ("test1", "test2", ".gh"):
            (self.sandbox / name / "xyz").mkdir(parents=True)
            (self.sandbox / name / "xyz" / "f.txt").write_text("x")
        self.env_file = self.lab_root / ".env"
        self.write_env()
        self.cfg = env.load(self.env_file, {})

    def write_env(self, extra=""):
        self.env_file.write_text(
            f"LAB_GH_ORG=lab-org\nGH_TOKEN={GH_TOKEN}\nGH_TOKEN_EXPIRES_AT=2026-10-13T00:00:00+00:00\n"
            f"CLAUDE_CODE_OAUTH_TOKEN={OAUTH_TOKEN}\n{extra}"
        )
        self.env_file.chmod(0o600)

    def run_teardown(self, gh, *, secrets=False, confirm=lambda q: True, revoke=None, user=(401,),
                     claude_rc=1, claude_stdout="", claude_stderr=REVOKED_STDERR,
                     claude_raises=None, tty=False, environ=None):
        """Run teardown.run with HTTP, `claude`, open_url, sleep and stdin replaced.

        `user` scripts the answers to GET /user with the old GitHub token, one
        per call (the last one sticks): an HTTP status code or an exception.
        """
        self.asked, self.requests, self.claude_calls, self.opened = [], [], [], []
        self.config_dir_existed = []
        answers = list(user)

        def ask(question):
            self.asked.append(question)
            return confirm(question)

        def urlopen(request, timeout=None):
            self.requests.append(request)
            if request.full_url == REVOKE_URL:
                if isinstance(revoke, Exception):
                    raise revoke
                return mock.MagicMock()
            answer = answers.pop(0) if len(answers) > 1 else answers[0]
            if isinstance(answer, Exception):
                raise answer
            if answer == 200:
                resp = mock.MagicMock()
                resp.status = 200
                resp.__enter__.return_value = resp
                return resp
            err = urllib.error.HTTPError(request.full_url, answer, "x", {}, io.BytesIO())
            self.addCleanup(err.close)
            raise err

        def fake_run(argv, **kwargs):
            self.claude_calls.append((argv, kwargs))
            self.config_dir_existed.append(Path(kwargs["env"]["CLAUDE_CONFIG_DIR"]).is_dir())
            if claude_raises:
                raise claude_raises
            return subprocess.CompletedProcess(argv, claude_rc, claude_stdout, claude_stderr)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(teardown.urllib.request, "urlopen", side_effect=urlopen), \
             mock.patch.object(teardown.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(teardown.time, "sleep") as sleep, \
             mock.patch.object(teardown, "open_url", side_effect=self.opened.append), \
             mock.patch.object(teardown.sys.stdin, "isatty", return_value=tty), \
             mock.patch("builtins.input", return_value="") as inp, \
             mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/outside", "ANTHROPIC_API_KEY": "outside",
                                          **(environ or {})}), \
             redirect_stdout(out), redirect_stderr(err):
            code = teardown.run(self.lab_root, self.cfg, gh, secrets=secrets, confirm=ask, now=NOW)
        self.input_calls = inp.call_count
        self.sleeps = sleep.call_count
        self.out, self.err = out.getvalue(), err.getvalue()
        return code

    def revoke_requests(self):
        return [r for r in self.requests if r.full_url == REVOKE_URL]

    def user_requests(self):
        return [r for r in self.requests if r.full_url == USER_URL]

    def assert_no_token_shown(self):
        for token in (GH_TOKEN, OAUTH_TOKEN):
            self.assertNotIn(token, self.out)
            self.assertNotIn(token, self.err)


class Delete(Base):
    def test_no_delete_without_confirm(self):
        gh = FakeGitHub(["lab-test1"])
        code = self.run_teardown(gh, secrets=True, confirm=lambda q: False)
        self.assertEqual(code, 1)
        self.assertEqual(gh.deleted(), [])
        self.assertTrue((self.sandbox / "test1" / "xyz" / "f.txt").exists())
        self.assertEqual(self.requests, [])  # nothing revoked either
        self.assertEqual(self.claude_calls, [])
        self.assertIn("GH_TOKEN", self.env_file.read_text())

    def test_prints_plan_and_asks_once(self):
        gh = FakeGitHub(["lab-test1", "other"])
        self.run_teardown(gh)
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(
            self.asked[0],
            "Delete 1 repos and 3 sandbox directories (incl. run transcripts under results/)? [y/N]",
        )
        self.assertIn("lab-test1", self.out)
        self.assertNotIn("other", self.out)
        for name in ("test1", "test2", ".gh"):
            self.assertIn(str(self.sandbox / name), self.out)

    def test_confirmed_deletes_repos_and_dirs(self):
        gh = FakeGitHub(["lab-test1", "other", "lab-s5check"])
        code = self.run_teardown(gh)
        self.assertEqual(code, 0)
        self.assertEqual(gh.deleted(), ["lab-test1", "lab-s5check"])
        self.assertEqual(list(self.sandbox.iterdir()), [])
        self.assertTrue(self.sandbox.exists())
        # Without --secrets the credentials stay.
        self.assertEqual(self.requests, [])
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", self.env_file.read_text())

    def test_failed_repo_delete_is_reported_and_keeps_the_github_token(self):
        gh = FakeGitHub(["lab-test1", "lab-test2"], delete_fails={"lab-test1"})
        code = self.run_teardown(gh, secrets=True)
        self.assertEqual(code, 1)
        self.assertEqual(gh.deleted(), ["lab-test1", "lab-test2"])
        self.assertEqual(self.requests, [])  # the token is still needed to retry
        self.assertIn("GH_TOKEN=", self.env_file.read_text())
        self.assertEqual(list(self.sandbox.iterdir()), [])

    def test_nothing_to_delete_asks_nothing(self):
        for child in self.sandbox.iterdir():
            subprocess.run(["rm", "-rf", str(child)], check=True)
        code = self.run_teardown(FakeGitHub())
        self.assertEqual(code, 0)
        self.assertEqual(self.asked, [])


class Secrets(Base):
    def test_secrets_removed_and_revoke_called(self):
        self.write_env("LAB_GH_ORG=lab-org\n")
        self.cfg = env.load(self.env_file, {})
        code = self.run_teardown(FakeGitHub(), secrets=True)
        self.assertEqual(code, 0)
        (request,) = self.revoke_requests()
        self.assertEqual(request.full_url, REVOKE_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"credentials": [GH_TOKEN]})
        self.assertFalse(request.has_header("Authorization"))
        remaining = env.load(self.env_file, {})
        self.assertNotIn("GH_TOKEN", remaining)
        self.assertNotIn("GH_TOKEN_EXPIRES_AT", remaining)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", remaining)
        self.assertEqual(remaining["LAB_GH_ORG"], "lab-org")
        self.assertNotIn(GH_TOKEN, self.out)
        self.assertNotIn(OAUTH_TOKEN, self.out)

    def test_env_file_deleted_when_no_keys_are_left(self):
        self.env_file.write_text(
            f"# lab\nGH_TOKEN={GH_TOKEN}\nGH_TOKEN_EXPIRES_AT=x\nCLAUDE_CODE_OAUTH_TOKEN={OAUTH_TOKEN}\n"
        )
        self.cfg = env.load(self.env_file, {"LAB_GH_ORG": "lab-org"})  # the org set in the environment
        self.run_teardown(FakeGitHub(), secrets=True)
        self.assertFalse(self.env_file.exists())

    def test_failed_revoke_request_still_verifies_and_drops_the_token(self):
        err = urllib.error.HTTPError(REVOKE_URL, 500, "Server Error", {}, io.BytesIO())
        self.addCleanup(err.close)
        code = self.run_teardown(FakeGitHub(), secrets=True, revoke=err, user=[200])
        self.assertEqual(code, 1)
        self.assertTrue(self.user_requests())  # the check decides, not the revoke request
        text = self.env_file.read_text()
        self.assertNotIn("GH_TOKEN=", text)
        self.assertNotIn("GH_TOKEN_EXPIRES_AT=", text)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", text)  # the Claude step still ran
        self.assertIn(f"The GitHub token {env.mask(GH_TOKEN)} is still active", self.out)
        self.assert_no_token_shown()

    def test_failed_revoke_request_with_a_dead_token_counts_as_revoked(self):
        code = self.run_teardown(FakeGitHub(), secrets=True, revoke=urllib.error.URLError("dns"), user=[401])
        self.assertEqual(code, 0)
        self.assertNotIn("GH_TOKEN=", self.env_file.read_text())

    def test_claude_step_opens_page_and_waits_for_enter_on_a_terminal(self):
        self.run_teardown(FakeGitHub(), secrets=True, tty=True)
        self.assertEqual(self.opened, [CLAUDE_URL])
        self.assertIn("Revoke every 'Claude Code' entry the lab created, then press Enter", self.out)
        self.assertEqual(self.input_calls, 1)

    def test_claude_step_does_not_wait_without_a_terminal(self):
        self.run_teardown(FakeGitHub(), secrets=True, tty=False)
        self.assertEqual(self.input_calls, 0)
        self.assertEqual(len(self.claude_calls), 1)

    def test_claude_check_uses_the_old_token_in_a_clean_environment(self):
        self.run_teardown(
            FakeGitHub(), secrets=True,
            environ={"GH_TOKEN": "ghp_outside", "GITHUB_TOKEN": "outside", "GH_ENTERPRISE_TOKEN": "outside"},
        )  # fmt: skip
        (argv, kwargs), = self.claude_calls
        self.assertEqual(
            argv,
            ["claude", "-p", "Reply with OK", "--model", "claude-haiku-5-5", "--permission-mode", "default",
             "--output-format", "stream-json", "--verbose"],
        )  # fmt: skip
        child = kwargs["env"]
        self.assertEqual(child["CLAUDE_CODE_OAUTH_TOKEN"], OAUTH_TOKEN)
        for gone in ("ANTHROPIC_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"):
            self.assertNotIn(gone, child)
        self.assertNotEqual(child["CLAUDE_CONFIG_DIR"], "/outside")
        self.assertEqual(self.config_dir_existed, [True])  # created for the call
        self.assertFalse(Path(child["CLAUDE_CONFIG_DIR"]).exists())  # and thrown away after it
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 120)
        self.assertTrue(kwargs["capture_output"])
        self.assertNotIn(OAUTH_TOKEN, " ".join(argv))

    def test_claude_check_uses_the_probe_model_from_the_config(self):
        self.cfg["LAB_PROBE_MODEL"] = "claude-sonnet-5-5"
        self.run_teardown(FakeGitHub(), secrets=True)
        (argv, _), = self.claude_calls
        self.assertEqual(argv[argv.index("--model") + 1], "claude-sonnet-5-5")

    def claude_not_verified(self):
        return (
            f"Could not verify that the Claude token {env.mask(OAUTH_TOKEN)} was revoked. Open {CLAUDE_URL}"
            ' and make sure every "Claude Code" entry the lab created is revoked.'
        )

    def test_claude_auth_failure_in_stderr_means_revoked(self):
        code = self.run_teardown(FakeGitHub(), secrets=True, claude_rc=1, claude_stderr=REVOKED_STDERR)
        self.assertIn(f"Claude token {env.mask(OAUTH_TOKEN)}: revoked\n", self.out)
        self.assertEqual(code, 0)
        self.assert_no_token_shown()

    def test_every_auth_phrase_means_revoked(self):
        phrases = (
            "API Error: 401 {}", "HTTP 401 Unauthorized", "status: 401", "authentication_error", "Invalid bearer token", "invalid x-api-key",
            "Invalid API key · Please run /login", "OAuth token has been revoked", "Token has been revoked",
            "OAuth token has expired", "OAUTH TOKEN IS INVALID",
        )  # fmt: skip
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.write_env()
                code = self.run_teardown(FakeGitHub(), secrets=True, claude_rc=1, claude_stderr=f"{phrase}\n")
                self.assertIn("revoked\n", self.out)
                self.assertNotIn("Could not verify", self.out)
                self.assertEqual(code, 0)

    def test_claude_auth_failure_in_result_event_means_revoked(self):
        result = json.dumps({"type": "result", "is_error": True, "result": "Invalid bearer token"})
        code = self.run_teardown(
            FakeGitHub(), secrets=True, claude_rc=1, claude_stdout=f"{result}\n", claude_stderr=""
        )
        self.assertIn(f"Claude token {env.mask(OAUTH_TOKEN)}: revoked\n", self.out)
        self.assertEqual(code, 0)

    def test_other_claude_failure_is_not_verified(self):
        failures = (
            "API Error: 400 invalid model: claude-haiku-5-5", "invalid_request_error", "API Error: 529 overloaded",
            "", "Error: usage limit reached", "invalid", "HTTP 4010",
            "    at run (/app/cli.js:401:12)", "Error at line 401", "exit 401",
        )  # fmt: skip
        for stderr in failures:
            with self.subTest(stderr=stderr):
                self.write_env()
                code = self.run_teardown(FakeGitHub(), secrets=True, claude_rc=1, claude_stderr=f"{stderr}\n")
                self.assertIn(self.claude_not_verified(), self.out)
                self.assertNotIn(f"Claude token {env.mask(OAUTH_TOKEN)}: revoked", self.out)
                self.assertIn("Claude token removed from .env", self.out)
                self.assertEqual(code, 1)
                self.assert_no_token_shown()

    def test_other_failure_in_result_event_is_not_verified(self):
        result = json.dumps({"type": "result", "is_error": True, "result": "invalid_request_error: invalid model"})
        code = self.run_teardown(
            FakeGitHub(), secrets=True, claude_rc=1, claude_stdout=f"{result}\n", claude_stderr=""
        )
        self.assertIn(self.claude_not_verified(), self.out)
        self.assertEqual(code, 1)

    def test_claude_success_means_still_active(self):
        code = self.run_teardown(FakeGitHub(), secrets=True, claude_rc=0, claude_stderr="")
        self.assertIn(
            f"The Claude token {env.mask(OAUTH_TOKEN)} is still active. Revoke it at {CLAUDE_URL}.", self.out
        )
        self.assertEqual(code, 1)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", self.env_file.read_text())
        self.assert_no_token_shown()

    def test_claude_check_that_cannot_run_says_so(self):
        code = self.run_teardown(
            FakeGitHub(), secrets=True, claude_raises=subprocess.TimeoutExpired("claude", 120)
        )
        self.assertIn(self.claude_not_verified(), self.out)
        self.assertNotIn("still active", self.out)
        self.assertEqual(code, 1)

    def test_missing_claude_token_skips_that_step(self):
        env.remove(self.env_file, "CLAUDE_CODE_OAUTH_TOKEN")
        self.cfg = env.load(self.env_file, {})
        self.run_teardown(FakeGitHub(), secrets=True)
        self.assertEqual(self.claude_calls, [])
        self.assertEqual(self.opened, [])


class GitHubRevocation(Base):
    """After the revoke request, GET /user with the old token decides what is reported."""

    def masked(self):
        return env.mask(GH_TOKEN)

    def assert_dropped_from_env(self):
        remaining = env.load(self.env_file, {})
        self.assertNotIn("GH_TOKEN", remaining)
        self.assertNotIn("GH_TOKEN_EXPIRES_AT", remaining)

    def test_401_means_revoked(self):
        code = self.run_teardown(FakeGitHub(), secrets=True, user=[401])
        self.assertEqual(code, 0)
        self.assertIn(f"GitHub token {self.masked()}: revoked and removed from .env", self.out)
        self.assert_dropped_from_env()
        self.assert_no_token_shown()

    def test_check_sends_the_token_only_in_the_authorization_header(self):
        self.run_teardown(FakeGitHub(), secrets=True, user=[401])
        (revoke,) = self.revoke_requests()
        self.assertFalse(revoke.has_header("Authorization"))
        (check,) = self.user_requests()
        self.assertEqual(check.get_method(), "GET")
        self.assertIsNone(check.data)
        self.assertEqual(check.get_header("Authorization"), f"Bearer {GH_TOKEN}")
        self.assertNotIn(GH_TOKEN, check.full_url)
        others = {k: v for k, v in check.header_items() if k.lower() != "authorization"}
        self.assertNotIn(GH_TOKEN, repr(others))
        # No process ever sees the GitHub token in argv.
        for argv, _ in self.claude_calls:
            self.assertNotIn(GH_TOKEN, " ".join(argv))

    def test_200_means_still_active(self):
        code = self.run_teardown(FakeGitHub(), secrets=True, user=[200])
        self.assertEqual(code, 1)
        self.assertIn(
            f"The GitHub token {self.masked()} is still active. It was removed from .env;"
            f" revoke it at {GITHUB_TOKENS_URL}.",
            self.out,
        )
        self.assertNotIn(f"GitHub token {self.masked()}: revoked", self.out)
        self.assertGreater(len(self.user_requests()), 1)  # asked again before giving up
        self.assert_dropped_from_env()
        self.assert_no_token_shown()

    def test_revocation_that_lands_a_little_later_counts(self):
        # The revoke endpoint answers 202 Accepted; the token may die a moment later.
        code = self.run_teardown(FakeGitHub(), secrets=True, user=[200, 401])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.user_requests()), 2)
        self.assertEqual(self.sleeps, 1)
        self.assertIn(f"GitHub token {self.masked()}: revoked", self.out)

    def test_other_answers_are_not_verified(self):
        for answer in (500, 403, 404, urllib.error.URLError("dns"), OSError("reset"), TimeoutError()):
            with self.subTest(answer=answer):
                self.write_env()
                self.cfg = env.load(self.env_file, {})
                code = self.run_teardown(FakeGitHub(), secrets=True, user=[answer])
                self.assertEqual(code, 1)
                self.assertIn(
                    f"Could not verify that the GitHub token {self.masked()} was revoked. It was removed"
                    f" from .env; open {GITHUB_TOKENS_URL} and make sure the lab's token is revoked.",
                    self.out,
                )
                self.assertNotIn(f"GitHub token {self.masked()} is still active", self.out)
                self.assert_dropped_from_env()
                self.assert_no_token_shown()


class WithoutGitHub(Base):
    """Teardown with what is left: an expired or missing GitHub token, no Claude token."""

    def test_expired_github_token_skips_repos_but_still_cleans_up(self):
        self.cfg["GH_TOKEN_EXPIRES_AT"] = "2026-10-12T08:00:00+00:00"  # before NOW
        gh = FakeGitHub(["lab-test1"])
        code = self.run_teardown(gh, secrets=True)
        self.assertEqual(gh.calls, [])  # the dead token is never used
        self.assertIn("python3 lab.py tokens github", self.out)
        self.assertIn("lab repos cannot be listed or deleted", self.out)
        self.assertEqual(list(self.sandbox.iterdir()), [])  # sandbox dirs still deleted
        self.assertEqual(self.requests, [])  # no GitHub revocation either
        self.assertEqual(len(self.claude_calls), 1)  # the Claude step still runs
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", self.env_file.read_text())
        self.assertIn("GH_TOKEN=", self.env_file.read_text())
        self.assertEqual(code, 1)

    def test_missing_github_token_or_org_skips_repos(self):
        for key in ("GH_TOKEN", "LAB_GH_ORG"):
            with self.subTest(key=key):
                cfg = dict(self.cfg)
                del cfg[key]
                self.cfg = cfg
                gh = FakeGitHub(["lab-test1"])
                self.run_teardown(gh)
                self.assertEqual(gh.calls, [])
                self.assertIn("lab repos cannot be listed or deleted", self.out)
                self.cfg = env.load(self.env_file, {})

    def test_listing_failure_skips_repos_but_still_cleans_up(self):
        gh = FakeGitHub()
        gh.list_repos = mock.Mock(side_effect=GitHubError("list repos: HTTP 401 Bad credentials"))
        code = self.run_teardown(gh)
        self.assertIn("HTTP 401", self.out)
        self.assertIn("lab repos cannot be listed or deleted", self.out)
        self.assertEqual(list(self.sandbox.iterdir()), [])
        self.assertEqual(code, 1)

    def test_rerun_without_claude_token_deletes_repos_and_revokes_github(self):
        """After a failed repo delete the Claude token is gone but the GitHub token stays."""
        env.remove(self.env_file, "CLAUDE_CODE_OAUTH_TOKEN")
        self.cfg = env.load(self.env_file, {})
        gh = FakeGitHub(["lab-test1"])
        code = self.run_teardown(gh, secrets=True)
        self.assertEqual(gh.deleted(), ["lab-test1"])
        (request,) = self.revoke_requests()
        self.assertEqual(json.loads(request.data), {"credentials": [GH_TOKEN]})
        self.assertEqual(self.claude_calls, [])
        self.assertEqual(code, 0)


def load_cli():
    spec = importlib.util.spec_from_file_location("lab_cli", REPO / "lab.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


class Cli(unittest.TestCase):
    """The new lab.py commands with every outside call replaced."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lab_root = Path(tmp.name)
        self.cli = load_cli()
        self.cli.LAB_ROOT = self.lab_root
        self.cli.SANDBOX = self.lab_root / "sandbox"
        self.cfg = {"LAB_GH_ORG": "lab-org", "GH_TOKEN": GH_TOKEN, "CLAUDE_CODE_OAUTH_TOKEN": OAUTH_TOKEN}

    def call(self, fn, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = fn(*args, **kwargs)
        return code, out.getvalue(), err.getvalue()

    # report

    def test_report_without_results_prints_a_hint(self):
        code, out, _ = self.call(self.cli.run_report)
        self.assertEqual(code, 0)
        self.assertEqual(out, "no results yet — run python3 lab.py run …\n")

    def test_report_renders_the_results_file(self):
        with mock.patch.object(self.cli.results, "read", return_value=[{"test": "test1"}]) as read, \
             mock.patch.object(self.cli.report, "render", return_value="RENDERED") as render:
            code, out, _ = self.call(self.cli.run_report)
        read.assert_called_once_with(self.lab_root / "results.csv")
        render.assert_called_once_with([{"test": "test1"}])
        self.assertEqual((code, out), (0, "RENDERED\n"))

    # setup

    def setup_cmd(self, *, org_ok=True, probe=None, matrix=None):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0)

        gh = mock.MagicMock()
        gh.org_ok.return_value = org_ok
        with mock.patch.object(self.cli.github, "GitHub", return_value=gh), \
             mock.patch.object(self.cli.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(self.cli.preflight, "chrome_installed") as chrome_mock, \
             mock.patch.object(self.cli.limits, "probe", return_value=probe) as probe_mock:
            if matrix is not None:
                matrix_file = self.lab_root / "lab" / "matrix.conf"
                matrix_file.parent.mkdir()
                matrix_file.write_text(matrix)
                self.cli.MATRIX_FILE = matrix_file
                (self.lab_root / "lab" / "profiles").symlink_to(REPO / "lab" / "profiles")
                for name in ("prompt-simple.md", "prompt-well-defined.md"):
                    (self.lab_root / name).write_text((REPO / name).read_text())
            result = self.call(self.cli.run_setup, self.cfg)
        return result, calls, probe_mock, chrome_mock

    def test_setup_creates_dirs_and_prints_matrix(self):
        (code, out, err), calls, probe, _ = self.setup_cmd(probe={"five_hour_pct": 12, "seven_day_pct": 5})
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])  # nothing is installed or run
        self.assertEqual(err, "")
        tests = config.load_matrix(REPO / "lab" / "matrix.conf", {})
        for test in tests:
            self.assertTrue((self.lab_root / "sandbox" / test.id).is_dir())
            self.assertIn(test.id, out)
            self.assertIn(test.model, out)
        self.assertIn("5-hour limit: 88 % left (7-day: 95 % left)", out)
        probe.assert_called_once()

    def test_setup_leaves_chrome_to_the_preflight_check(self):
        # prepare() stops every command that runs tests without Chrome; setup does not check again.
        (code, _, _), _, _, chrome = self.setup_cmd()
        self.assertEqual(code, 0)
        chrome.assert_not_called()

    def test_setup_prints_the_goal(self):
        goal = self.lab_root / "prompt-goal.toml"
        goal.write_text('[[check]]\ntype = "pages_live"\nwait_s = 30\n')
        self.cli.GOAL_FILE = goal
        (code, out, _), _, _, _ = self.setup_cmd()
        self.assertEqual(code, 0)
        self.assertIn("Goal (prompt-goal.toml): pages_live (wait up to 30 s)", out)

    def test_setup_stops_on_a_bad_goal_file(self):
        bad = self.lab_root / "prompt-goal.toml"
        bad.write_text('[[check]]\ntype = "app_works"\n')
        self.cli.GOAL_FILE = bad
        (code, _, err), _, probe, _ = self.setup_cmd()
        self.assertEqual(code, 1)
        self.assertIn("unknown type 'app_works'", err)
        probe.assert_not_called()

    def test_setup_prints_the_effort_of_a_test(self):
        matrix = "test1 clean prompt-well-defined.md claude-sonnet-5 medium\ntest2 clean\n"
        (code, out, _), _, _, _ = self.setup_cmd(matrix=matrix)
        self.assertEqual(code, 0)
        line1, line2 = (next(l for l in out.splitlines() if l.split()[:1] == [t]) for t in ("test1", "test2"))
        self.assertEqual(line1.split()[2:], ["prompt-well-defined.md", "claude-sonnet-5", "medium"])
        self.assertEqual(line2.split()[2:], ["prompt-simple.md", config.DEFAULT_MODEL])
        # The model column lines up although the prompt file names differ in length.
        self.assertEqual(line1.index("claude-sonnet-5"), line2.index(config.DEFAULT_MODEL))

    def test_setup_prints_the_limit_as_unknown_when_the_probe_fails(self):
        (_, out, _), _, _, _ = self.setup_cmd(probe=None)
        self.assertIn("5-hour limit: unknown", out)

    def test_setup_stops_when_the_org_is_unreachable(self):
        (code, _, err), calls, probe, _ = self.setup_cmd(org_ok=False)
        self.assertEqual(code, 1)
        self.assertIn("lab-org", err)
        self.assertEqual(calls, [])
        probe.assert_not_called()
        self.assertFalse((self.lab_root / "sandbox").exists())

    # teardown

    def teardown_cmd(self, *, yes, tty, answer=True, secrets=False):
        captured = {}

        def fake_run(lab_root, cfg, gh, *, secrets, confirm):
            captured["confirm"] = confirm
            captured["secrets"] = secrets
            captured["answer"] = confirm("Delete?")
            return 0

        with mock.patch.object(self.cli.teardown, "run", side_effect=fake_run), \
             mock.patch.object(self.cli.github, "GitHub"), \
             mock.patch.object(self.cli, "ask_yes_no", return_value=answer) as ask, \
             mock.patch.object(self.cli.sys.stdin, "isatty", return_value=tty):
            code, out, err = self.call(self.cli.run_teardown, self.cfg, secrets=secrets, yes=yes)
        return captured, ask, out, err, code

    def test_teardown_yes_skips_the_question(self):
        captured, ask, _, _, code = self.teardown_cmd(yes=True, tty=False, secrets=True)
        self.assertTrue(captured["answer"])
        self.assertTrue(captured["secrets"])
        ask.assert_not_called()
        self.assertEqual(code, 0)

    def test_teardown_asks_on_a_terminal(self):
        captured, ask, _, _, _ = self.teardown_cmd(yes=False, tty=True, answer=False)
        self.assertFalse(captured["answer"])
        ask.assert_called_once_with("Delete?")

    def test_teardown_without_terminal_and_without_yes_stops(self):
        captured, ask, out, _, _ = self.teardown_cmd(yes=False, tty=False)
        self.assertFalse(captured["answer"])
        ask.assert_not_called()
        self.assertIn("--yes", out)


if __name__ == "__main__":
    unittest.main()
