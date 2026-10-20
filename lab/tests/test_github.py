import contextlib
import http.client
import importlib.util
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labkit import env, github, github_auth, tokens  # noqa: E402


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.dir.name)
        self.path = self.tmp / ".env"

    def tearDown(self):
        self.dir.cleanup()


class EnvLoad(TempDirCase):
    def test_load_environment_wins(self):
        self.path.write_text("A=file\nB=file\n")
        self.assertEqual(env.load(self.path, {"A": "env"}), {"A": "env", "B": "file"})

    def test_load_ignores_comments_and_quotes(self):
        self.path.write_text('# c\n\nK="v w"\n')
        self.assertEqual(env.load(self.path, {}), {"K": "v w"})

    def test_load_without_file_returns_environ(self):
        self.assertEqual(env.load(self.path, {"A": "env"}), {"A": "env"})


class EnvRemove(TempDirCase):
    def test_remove_keeps_other_lines_and_mode(self):
        self.path.write_text("# c\nA=1\nK=2\nB=3\n")
        self.path.chmod(0o644)
        env.remove(self.path, "K")
        self.assertEqual(self.path.read_text(), "# c\nA=1\nB=3\n")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_remove_missing_file_is_noop(self):
        env.remove(self.path, "K")
        self.assertFalse(self.path.exists())


class OpenUrl(unittest.TestCase):
    def test_uses_lab_browser_and_prints_url(self):
        out = io.StringIO()
        browser = mock.Mock()
        with mock.patch.dict("os.environ", {"LAB_BROWSER": "open -a Firefox"}), \
             mock.patch.object(github_auth.webbrowser, "get", return_value=browser) as get, \
             contextlib.redirect_stdout(out):
            github_auth.open_url("https://example.invalid/x")
        get.assert_called_once_with("open -a Firefox %s")
        browser.open.assert_called_once_with("https://example.invalid/x")
        self.assertIn("https://example.invalid/x", out.getvalue())

    def test_default_browser(self):
        out = io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(github_auth.webbrowser, "open") as open_, \
             contextlib.redirect_stdout(out):
            github_auth.open_url("https://example.invalid/y")
        open_.assert_called_once_with("https://example.invalid/y")
        self.assertIn("https://example.invalid/y", out.getvalue())


class PersonalAccessToken(TempDirCase):
    def test_pat_url_contains_org_and_expiry(self):
        url = github_auth.pat_url("org1", suffix="3f9a")
        parts = urllib.parse.urlparse(url)
        self.assertEqual(
            f"{parts.scheme}://{parts.netloc}{parts.path}",
            "https://github.com/settings/personal-access-tokens/new",
        )
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        self.assertEqual(q["target_name"], "org1")
        self.assertEqual(q["expires_in"], "1")
        self.assertEqual(q["name"], "Claude Code Context Lab 3f9a")
        self.assertTrue(q["description"])
        permissions = {k: q[k] for k in ("administration", "contents", "pages", "workflows", "actions", "metadata")}
        self.assertEqual(
            permissions,
            {"administration": "write", "contents": "write", "pages": "write",
             "workflows": "write", "actions": "read", "metadata": "read"},
        )

    def test_pat_name_gets_random_suffix_by_default(self):
        # GitHub requires token names to be unique per user.
        def name():
            query = urllib.parse.urlparse(github_auth.pat_url("org1")).query
            return urllib.parse.parse_qs(query)["name"][0]

        self.assertRegex(name(), r"^Claude Code Context Lab [0-9a-f]{4}$")
        self.assertGreater(len({name() for _ in range(20)}), 1)

    def test_store_pat_writes_token_and_24h_expiry(self):
        now = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
        github_auth.store_pat(self.path, "github_pat_FAKE", now)
        self.assertEqual(
            self.path.read_text(),
            "GH_TOKEN=github_pat_FAKE\nGH_TOKEN_EXPIRES_AT=2026-10-13T08:00:00+00:00\n",
        )
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def create(self, typed):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(github_auth, "open_url") as open_url, \
             mock.patch.object(github_auth.getpass, "getpass", return_value=typed) as ask, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = github_auth.create_pat(
                self.path, "org1", datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc), suffix="3f9a"
            )
        open_url.assert_called_once_with(github_auth.pat_url("org1", suffix="3f9a"))
        ask.assert_called_once()
        return rc, out.getvalue(), err.getvalue()

    def test_create_pat_stores_and_masks(self):
        token = "github_pat_" + "A1b2C3d4" * 8
        rc, out, _ = self.create(f"  {token}\n")
        self.assertEqual(rc, 0)
        self.assertIn(f"GH_TOKEN={token}\n", self.path.read_text())
        self.assertNotIn(token, out)
        self.assertIn(env.mask(token), out)
        self.assertIn("2026-10-13T08:00:00+00:00", out)

    def test_create_pat_prints_checklist_before_prompt(self):
        events = []
        out = io.StringIO()
        with mock.patch.object(github_auth, "open_url"), \
             mock.patch.object(github_auth.getpass, "getpass",
                               side_effect=lambda *_: events.append(out.getvalue()) or ""), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            github_auth.create_pat(self.path, "org1", datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc))
        shown = events[0]  # what was on screen when the prompt appeared
        self.assertIn("Resource owner: org1", shown)
        self.assertIn("Expiration: 1 day", shown)
        self.assertIn("Repository access: All repositories", shown)
        self.assertIn("Read and write: Administration, Contents, Pages, Workflows", shown)
        self.assertIn("Read-only: Actions, Metadata", shown)
        self.assertIn("drops pre-filled values", shown)

    def test_create_pat_warns_about_screen_sharing_before_opening_the_form(self):
        out = io.StringIO()
        seen = []
        with mock.patch.object(github_auth, "open_url", side_effect=lambda url: seen.append(out.getvalue())), \
             mock.patch.object(github_auth.getpass, "getpass", return_value=""), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            github_auth.create_pat(self.path, "org1", datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc))
        self.assertIn("don't share or record your screen", seen[0])

    def test_create_pat_cancelled_at_the_prompt_stores_nothing(self):
        for exc in (EOFError, KeyboardInterrupt):
            with self.subTest(exc=exc.__name__):
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(github_auth, "open_url"), \
                     mock.patch.object(github_auth.getpass, "getpass", side_effect=exc), \
                     contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = github_auth.create_pat(
                        self.path, "org1", datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
                    )
                self.assertEqual(rc, 1)
                self.assertIn("Cancelled. Nothing was stored.", err.getvalue())
                self.assertFalse(self.path.exists())

    def test_create_pat_rejects_empty_and_wrong_prefix(self):
        for typed in ("", "   ", "ghu_notapat", "ghp_classictoken"):
            with self.subTest(typed=typed):
                rc, out, err = self.create(typed)
                self.assertEqual(rc, 1)
                self.assertIn("github_pat_", err)
                self.assertNotIn("ghu_notapat", out + err)
                self.assertNotIn("ghp_classictoken", out + err)
                self.assertFalse(self.path.exists())


class ConfigureRepo(TempDirCase):
    HERMETIC = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.tmp, capture_output=True, text=True, check=True,
            env={**os.environ, **self.HERMETIC},
        ).stdout.splitlines()  # fmt: skip

    def env(self):
        return github.gh_env(os.environ, "ghu_T", self.tmp / ".gh")

    def test_configure_repo_credential_helper(self):
        github.configure_repo(self.tmp, "https://github.com/org1/lab-x.git", self.env(), "ghu_T")
        self.assertEqual(
            self.git("config", "--local", "--get-all", "credential.helper"),
            ["", "!gh auth git-credential"],
        )
        self.assertEqual(
            self.git("config", "--local", "user.email"), ["lab@users.noreply.github.com"]
        )
        self.assertEqual(self.git("config", "--local", "user.name"), ["Claude Code Context Lab"])
        self.assertEqual(
            self.git("remote", "get-url", "origin"), ["https://github.com/org1/lab-x.git"]
        )
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD"), ["main"])

    def test_configure_repo_copies_no_hook_templates(self):
        github.configure_repo(self.tmp, "https://github.com/org1/lab-x.git", self.env(), "ghu_T")
        hooks = self.tmp / ".git" / "hooks"
        self.assertEqual(list(hooks.iterdir()) if hooks.exists() else [], [])

    def test_configure_repo_runs_git_isolated_with_a_timeout(self):
        ok = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(github.subprocess, "run", return_value=ok) as run:
            github.configure_repo(self.tmp, "https://github.com/org1/lab-x.git", self.env(), "ghu_T")
        argvs = [c.args[0] for c in run.call_args_list]
        self.assertEqual(argvs[0], ["git", "-c", "init.templateDir=", "init", "-b", "main"])
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["env"]["GIT_CONFIG_GLOBAL"], "/dev/null")
            self.assertEqual(call.kwargs["cwd"], self.tmp)
            self.assertTrue(call.kwargs["timeout"])

    def test_configure_repo_failure_names_git_stderr_without_token(self):
        github.configure_repo(self.tmp, "https://github.com/org1/lab-x.git", self.env(), "ghu_T")
        with self.assertRaises(github.GitHubError) as ctx:  # origin exists already
            github.configure_repo(self.tmp, "https://ghu_T@github.com/org1/lab-x.git", self.env(), "ghu_T")
        self.assertIn("git remote", str(ctx.exception))
        self.assertIn("already exists", str(ctx.exception))
        self.assertNotIn("ghu_T", str(ctx.exception))


class GhEnv(unittest.TestCase):
    def test_gh_env_isolates_user_login_and_git_config(self):
        cfg = Path("/x/sandbox/.gh")
        e = github.gh_env({"PATH": "/bin", "GH_TOKEN": "old", "GITHUB_TOKEN": "t2"}, "ghu_T", cfg)
        self.assertEqual(e["GH_TOKEN"], "ghu_T")
        self.assertEqual(e["GH_CONFIG_DIR"], str(cfg))
        self.assertEqual(e["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(e["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(e["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(e["PATH"], "/bin")
        self.assertNotIn("GITHUB_TOKEN", e)

    def test_gh_env_drops_claude_and_anthropic_variables(self):
        environ = {"PATH": "/bin", "CLAUDE_CODE_OAUTH_TOKEN": "sk-x", "CLAUDE_CONFIG_DIR": "/c",
                   "CLAUDECODE": "1", "ANTHROPIC_API_KEY": "k", "GH_ENTERPRISE_TOKEN": "t3"}
        e = github.gh_env(environ, "ghu_T", Path("/x/.gh"))
        self.assertEqual({k for k in e if k.startswith(("CLAUDE", "ANTHROPIC"))}, set())
        self.assertNotIn("GH_ENTERPRISE_TOKEN", e)
        self.assertEqual(e["PATH"], "/bin")

    def test_gh_env_drops_the_ssh_agent(self):
        environ = {"PATH": "/bin", "SSH_AUTH_SOCK": "/tmp/agent.sock", "SSH_AGENT_PID": "42"}
        e = github.gh_env(environ, "ghu_T", Path("/x/.gh"))
        self.assertNotIn("SSH_AUTH_SOCK", e)
        self.assertNotIn("SSH_AGENT_PID", e)
        self.assertEqual(e["PATH"], "/bin")

    def test_without_credentials_drops_the_ssh_agent(self):
        e = github.without_credentials({"SSH_AUTH_SOCK": "/s", "SSH_AGENT_PID": "1", "HOME": "/h"})
        self.assertEqual(e, {"HOME": "/h"})

    def test_without_credentials_drops_every_token(self):
        environ = {"PATH": "/bin", "GH_TOKEN": "a", "GITHUB_TOKEN": "b", "GH_ENTERPRISE_TOKEN": "c",
                   "GITHUB_ENTERPRISE_TOKEN": "d", "CLAUDE_X": "e", "ANTHROPIC_Y": "f"}
        self.assertEqual(github.without_credentials(environ), {"PATH": "/bin"})


class GitHubClient(TempDirCase):
    def make(self):
        return github.GitHub("org1", "ghu_T", self.tmp / ".gh")

    def run_result(self, rc=0, out="", err=""):
        return subprocess.CompletedProcess([], rc, out, err)

    def test_creates_config_dir_and_passes_token_via_env_only(self):
        gh = self.make()
        with mock.patch.object(github.subprocess, "run", return_value=self.run_result()) as run:
            gh.repo_exists("lab-x")
        argv = run.call_args.args[0]
        self.assertNotIn("ghu_T", " ".join(argv))
        self.assertEqual(run.call_args.kwargs["env"]["GH_TOKEN"], "ghu_T")
        self.assertTrue((self.tmp / ".gh").is_dir())
        self.assertEqual(
            Path(run.call_args.kwargs["env"]["GH_CONFIG_DIR"]).resolve(),
            (self.tmp / ".gh").resolve(),
        )

    def test_repo_exists_false_on_404(self):
        gh = self.make()
        with mock.patch.object(
            github.subprocess, "run", return_value=self.run_result(1, "", "gh: Not Found (HTTP 404)")
        ):
            self.assertFalse(gh.repo_exists("lab-x"))

    def test_create_repo_returns_clone_url(self):
        gh = self.make()
        with mock.patch.object(
            github.subprocess, "run",
            return_value=self.run_result(0, '{"clone_url": "https://github.com/org1/lab-x.git"}'),
        ) as run:
            self.assertEqual(gh.create_repo("lab-x"), "https://github.com/org1/lab-x.git")
        argv = run.call_args.args[0]
        self.assertIn("orgs/org1/repos", argv)
        self.assertIn("private=false", argv)

    def test_pages_status_none_on_404(self):
        gh = self.make()
        with mock.patch.object(
            github.subprocess, "run", return_value=self.run_result(1, "", "gh: Not Found (HTTP 404)")
        ):
            self.assertIsNone(gh.pages_status("lab-x"))

    def test_list_repos_filters_by_prefix(self):
        gh = self.make()
        with mock.patch.object(
            github.subprocess, "run", return_value=self.run_result(0, "lab-a\nlab-b\n")
        ):
            self.assertEqual(gh.list_repos("lab-"), ["lab-a", "lab-b"])

    def test_pages_ok_polls_until_built_then_checks_body(self):
        gh = self.make()
        statuses = iter([{"status": "building"}, {"status": "built", "html_url": "https://x.invalid/"}])
        body = mock.MagicMock()
        body.__enter__.return_value.status = 200
        body.__enter__.return_value.read.return_value = b"<h1>Hello GitHub Pages</h1>"
        with mock.patch.object(gh, "pages_status", side_effect=lambda n: next(statuses)), \
             mock.patch.object(github.time, "sleep") as sleep, \
             mock.patch.object(github.urllib.request, "urlopen", return_value=body):
            self.assertTrue(gh.pages_ok("lab-x", "Hello GitHub Pages", 60))
        sleep.assert_called_once_with(10)

    def test_pages_ok_false_on_timeout(self):
        gh = self.make()
        with mock.patch.object(gh, "pages_status", return_value={"status": "building"}), \
             mock.patch.object(github.time, "sleep"):
            self.assertFalse(gh.pages_ok("lab-x", "Hello", 30))


class PagesOk(TempDirCase):
    """pages_ok across legacy (branch) and workflow (GitHub Actions) Pages builds."""

    URL = "https://org1.github.io/lab-x/"

    def make(self):
        return github.GitHub("org1", "ghu_T", self.tmp / ".gh")

    @staticmethod
    def page(body=b"<h1>Hello GitHub Pages</h1>", status=200):
        resp = mock.MagicMock()
        resp.status = status
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        return resp

    def http_404(self):
        return urllib.error.HTTPError(self.URL, 404, "Not Found", {}, io.BytesIO())

    def check(self, statuses, fetches, wait_s):
        """Run pages_ok with scripted Pages statuses and HTTP outcomes; return (result, sleeps)."""
        gh = self.make()
        # An iterable side_effect raises the exception instances it contains.
        with mock.patch.object(gh, "pages_status", side_effect=iter(statuses)), \
             mock.patch.object(github.time, "sleep") as sleep, \
             mock.patch.object(github.urllib.request, "urlopen", side_effect=iter(fetches)):
            result = gh.pages_ok("lab-x", "Hello GitHub Pages", wait_s)
        return result, sleep.call_count

    def test_workflow_build_without_status_is_ok_on_first_poll(self):
        live = {"build_type": "workflow", "status": None, "html_url": self.URL}
        self.assertEqual(self.check([live], [self.page()], 180), (True, 0))

    def test_workflow_build_with_http_404_is_not_ok(self):
        live = {"build_type": "workflow", "status": None, "html_url": self.URL}
        self.assertEqual(self.check([live], [self.http_404()], 0), (False, 0))

    def test_workflow_build_without_text_is_not_ok(self):
        live = {"build_type": "workflow", "status": None, "html_url": self.URL}
        self.assertEqual(self.check([live], [self.page(b"<h1>other</h1>")], 0), (False, 0))

    def test_legacy_build_still_building_is_not_ok_even_with_200(self):
        building = {"build_type": "legacy", "status": "building", "html_url": self.URL}
        # No HTTP fetch is scripted: a legacy build that is not built must not be fetched.
        self.assertEqual(self.check([building], [], 0), (False, 0))

    def test_legacy_build_built_with_200_is_ok(self):
        built = {"build_type": "legacy", "status": "built", "html_url": self.URL}
        self.assertEqual(self.check([built], [self.page()], 180), (True, 0))

    def test_pages_not_enabled_yet_keeps_polling(self):
        live = {"build_type": "workflow", "status": None, "html_url": self.URL}
        self.assertEqual(self.check([None, live], [self.page()], 20), (True, 1))

    def test_http_failure_is_retried_on_next_poll(self):
        live = {"build_type": "workflow", "status": None, "html_url": self.URL}
        result = self.check([live, live], [self.http_404(), self.page()], 20)
        self.assertEqual(result, (True, 1))

    def test_network_error_does_not_raise(self):
        live = {"build_type": "workflow", "status": None, "html_url": self.URL}
        self.assertEqual(self.check([live], [urllib.error.URLError("dns")], 0), (False, 0))

    def test_broken_http_response_does_not_raise(self):
        live = {"build_type": "workflow", "status": None, "html_url": self.URL}
        for exc in (http.client.BadStatusLine("garbage"), http.client.IncompleteRead(b"<h1>Hel")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(self.check([live], [exc], 0), (False, 0))


class Homepage(GitHubClient):
    """GitHub.homepage: the repo's About -> Website field."""

    def homepage(self, rc=0, out="", err=""):
        gh = self.make()
        with mock.patch.object(github.subprocess, "run", return_value=self.run_result(rc, out, err)) as run:
            value = gh.homepage("lab-x")
        return value, run.call_args.args[0]

    def test_returns_the_website_field(self):
        value, argv = self.homepage(out='{"name": "lab-x", "homepage": "https://org1.github.io/lab-x/"}')
        self.assertEqual(value, "https://org1.github.io/lab-x/")
        self.assertIn("repos/org1/lab-x", argv)

    def test_empty_or_missing_field_is_none(self):
        for out in ('{"homepage": ""}', '{"homepage": null}', '{"homepage": "  "}', "{}"):
            with self.subTest(out=out):
                self.assertIsNone(self.homepage(out=out)[0])

    def test_missing_repo_is_none(self):
        self.assertIsNone(self.homepage(1, "", "gh: Not Found (HTTP 404)")[0])

    def test_other_failure_raises_without_the_token(self):
        with self.assertRaises(github.GitHubError) as ctx:
            self.homepage(1, "", "HTTP 500 for ghu_T")
        self.assertNotIn("ghu_T", str(ctx.exception))


class GoalOk(TempDirCase):
    """goal_ok: the Pages site is live AND the About -> Website field links to it."""

    URL = PagesOk.URL
    make = PagesOk.make
    page = staticmethod(PagesOk.page)
    http_404 = PagesOk.http_404

    def goal(self, statuses, fetches, homepages, wait_s=0, **checks):
        """Run goal_ok with scripted statuses, HTTP outcomes and homepages; return (result, sleeps)."""
        gh = self.make()
        checks = {"live": True, "homepage": True} | checks
        with mock.patch.object(gh, "pages_status", side_effect=iter(statuses)), \
             mock.patch.object(gh, "homepage", side_effect=iter(homepages)), \
             mock.patch.object(github.time, "sleep") as sleep, \
             mock.patch.object(github.urllib.request, "urlopen", side_effect=iter(fetches)):
            result = gh.goal_ok("lab-x", wait_s, **checks)
        return result, sleep.call_count

    def live(self, url=None):
        return {"build_type": "workflow", "status": None, "html_url": url or self.URL}

    def test_homepage_equal_to_the_pages_url_is_ok(self):
        self.assertEqual(self.goal([self.live()], [self.page()], [self.URL]), (True, 0))

    def test_homepage_variants_of_the_pages_url_are_ok(self):
        variants = [
            ("https://org1.github.io/lab-x/", "https://org1.github.io/lab-x"),
            ("https://org1.github.io/lab-x", "https://org1.github.io/lab-x/"),
            ("https://org1.github.io/lab-x/", "http://org1.github.io/lab-x/"),
            ("http://org1.github.io/lab-x/", "https://org1.github.io/lab-x"),
            ("https://org1.github.io/lab-x/", "  HTTPS://org1.github.io/lab-x/\n"),
        ]  # fmt: skip
        for html_url, homepage in variants:
            with self.subTest(html_url=html_url, homepage=homepage):
                result = self.goal([self.live(html_url)], [self.page()], [homepage])
                self.assertEqual(result, (True, 0))

    def test_missing_homepage_is_not_ok_even_if_the_page_is_live(self):
        for homepage in (None, ""):
            with self.subTest(homepage=homepage):
                self.assertEqual(self.goal([self.live()], [self.page()], [homepage]), (False, 0))

    def test_other_homepage_is_not_ok(self):
        others = ("https://example.com/", "https://org1.github.io/", "https://org1.github.io/lab-x//",
                  "https://org1.github.io/lab-x/index.html", "org1.github.io/lab-x/")
        for homepage in others:
            with self.subTest(homepage=homepage):
                self.assertEqual(self.goal([self.live()], [self.page()], [homepage]), (False, 0))

    def test_missing_page_is_not_ok_even_with_the_homepage_set(self):
        self.assertEqual(self.goal([None], [], [self.URL]), (False, 0))
        self.assertEqual(self.goal([self.live()], [self.http_404()], [self.URL]), (False, 0))

    def test_any_page_content_counts(self):
        for body in (b"<h1>TIC TAC TOE</h1>", b"<h1>other</h1>", b""):
            with self.subTest(body=body):
                self.assertEqual(self.goal([self.live()], [self.page(body)], [self.URL]), (True, 0))

    def test_missing_homepage_is_decided_without_waiting(self):
        # The page is already live: the About link only comes from an agent action, so
        # waiting for it would be pointless. One poll, no sleep, even with a long budget.
        for homepage in (None, "", "https://example.com/"):
            with self.subTest(homepage=homepage):
                result = self.goal([self.live()], [self.page()], [homepage], 180)
                self.assertEqual(result, (False, 0))

    def test_waits_for_the_page_and_decides_once_it_is_live(self):
        # Poll 1: no Pages site yet. Poll 2 (after 10 s): live, homepage set.
        result = self.goal([None, self.live()], [self.page()], [self.URL], 180)
        self.assertEqual(result, (True, 1))

    def test_page_live_late_without_homepage_ends_the_wait_there(self):
        building = {"build_type": "legacy", "status": "building", "html_url": self.URL}
        result = self.goal([building, self.live()], [self.page()], [None], 180)
        self.assertEqual(result, (False, 1))

    def test_waits_while_the_page_does_not_answer_then_gives_up(self):
        result = self.goal([self.live()] * 3, [self.http_404() for _ in range(3)], [], 20)
        self.assertEqual(result, (False, 2))

    def test_without_the_live_check_the_page_is_not_fetched(self):
        # No fetch is scripted: fetching would raise StopIteration.
        self.assertEqual(self.goal([self.live()], [], [self.URL], live=False), (True, 0))
        self.assertEqual(self.goal([self.live()], [], ["https://example.com/"], live=False), (False, 0))

    def test_without_the_live_check_a_building_site_is_decided_by_its_url(self):
        # The Pages URL exists as soon as Pages is configured; the build does not matter.
        building = {"build_type": "legacy", "status": "building", "html_url": self.URL}
        self.assertEqual(self.goal([building], [], [self.URL], live=False), (True, 0))
        self.assertEqual(self.goal([building], [], [None], live=False), (False, 0))

    def test_without_the_live_check_no_pages_is_not_ok(self):
        self.assertEqual(self.goal([None], [], [], 180, live=False), (False, 0))

    def test_without_the_homepage_check_a_live_page_is_enough(self):
        # No homepage is scripted: reading it would raise StopIteration.
        self.assertEqual(self.goal([self.live()], [self.page()], [], homepage=False), (True, 0))
        self.assertEqual(self.goal([self.live()], [self.http_404()], [], homepage=False), (False, 0))

    def test_host_is_compared_case_insensitively_but_the_path_is_not(self):
        upper = "https://MyOrg.GitHub.io/lab-test1/"
        lower = "https://myorg.github.io/lab-test1"
        self.assertTrue(github._same_site(upper, lower))
        self.assertTrue(github._same_site(lower, upper))
        self.assertFalse(github._same_site("https://myorg.github.io/Lab-Test1", lower))
        self.assertEqual(self.goal([self.live(lower)], [self.page()], [upper]), (True, 0))


class CheckS5(TempDirCase):
    def test_check_s5_reports_each_step_and_hides_token(self):
        fake = mock.Mock(spec=github.GitHub)
        fake.org_ok.return_value = True
        fake.repo_exists.return_value = False
        fake.create_repo.return_value = "https://github.com/org1/lab-s5check.git"
        fake.pages_ok.return_value = True
        cfg = {"LAB_GH_ORG": "org1", "GH_TOKEN": "ghu_SECRET"}
        pushed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(github, "GitHub", return_value=fake), \
             mock.patch.object(github, "configure_repo"), \
             mock.patch.object(github.subprocess, "run", return_value=pushed):
            rows = github.check_s5(cfg, self.tmp)
        self.assertTrue(rows)
        self.assertTrue(all(ok for _, ok, _ in rows), rows)
        self.assertNotIn("ghu_SECRET", repr(rows))
        fake.delete_repo.assert_called_once_with("lab-s5check")

    def test_check_s5_cleans_up_when_push_fails(self):
        fake = mock.Mock(spec=github.GitHub)
        fake.org_ok.return_value = True
        fake.repo_exists.return_value = False
        fake.create_repo.return_value = "https://github.com/org1/lab-s5check.git"
        cfg = {"LAB_GH_ORG": "org1", "GH_TOKEN": "ghu_SECRET"}

        def git(argv, **kwargs):  # only the push fails
            if "push" in argv:
                return subprocess.CompletedProcess(argv, 1, "", "remote: denied for ghu_SECRET")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(github, "GitHub", return_value=fake), \
             mock.patch.object(github, "configure_repo"), \
             mock.patch.object(github.subprocess, "run", side_effect=git) as run:
            rows = github.check_s5(cfg, self.tmp)
        self.assertEqual(
            [(step, ok) for step, ok, _ in rows],
            [("org access", True), ("create repo", True), ("commit", True),
             ("push over HTTPS", False), ("delete repo", True)],
        )  # fmt: skip
        self.assertIn("remote: denied for ***", rows[3][2])
        self.assertTrue(any("push" in c.args[0] for c in run.call_args_list))
        self.assertNotIn("ghu_SECRET", repr(rows))
        fake.enable_pages.assert_not_called()
        fake.delete_repo.assert_called_once_with("lab-s5check")


    def check_with_real_files(self, fail_push: bool):
        """Run check_s5 in a sandbox dir next to a bystander dir and a sibling test dir."""
        sandbox = self.tmp / "sandbox"
        sandbox.mkdir()
        bystander = self.tmp / "keep-me"  # next to the sandbox: must survive
        bystander.mkdir()
        other = sandbox / "other-test"  # a sibling inside the sandbox: must survive
        other.mkdir()
        fake = mock.Mock(spec=github.GitHub)
        fake.org_ok.return_value = True
        fake.repo_exists.return_value = False
        fake.create_repo.return_value = "https://github.com/org1/lab-s5check.git"
        fake.pages_ok.return_value = True
        cfg = {"LAB_GH_ORG": "org1", "GH_TOKEN": "ghu_SECRET"}

        def git(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1 if fail_push and "push" in argv else 0, "", "")

        with mock.patch.object(github, "GitHub", return_value=fake), \
             mock.patch.object(github, "configure_repo"), \
             mock.patch.object(github.subprocess, "run", side_effect=git):
            rows = github.check_s5(cfg, sandbox)
        return sandbox, rows, bystander, other

    def test_check_s5_removes_its_local_copy_after_success(self):
        sandbox, rows, bystander, other = self.check_with_real_files(fail_push=False)
        self.assertTrue(all(ok for _, ok, _ in rows), rows)
        self.assertFalse((sandbox / github.S5_REPO).exists())
        self.assertTrue(sandbox.is_dir() and bystander.is_dir() and other.is_dir())

    def test_check_s5_removes_its_local_copy_after_a_failed_step(self):
        sandbox, rows, bystander, other = self.check_with_real_files(fail_push=True)
        self.assertFalse(all(ok for _, ok, _ in rows))
        self.assertFalse((sandbox / github.S5_REPO).exists())
        self.assertTrue(sandbox.is_dir() and bystander.is_dir() and other.is_dir())

    def test_check_s5_never_removes_a_copy_that_leads_out_of_the_sandbox(self):
        sandbox = self.tmp / "sandbox"
        sandbox.mkdir()
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("keep")
        (sandbox / github.S5_REPO).symlink_to(outside, target_is_directory=True)
        fake = mock.Mock(spec=github.GitHub)
        fake.org_ok.return_value = False  # stop after the first step
        with mock.patch.object(github, "GitHub", return_value=fake):
            github.check_s5({"LAB_GH_ORG": "org1", "GH_TOKEN": "ghu_SECRET"}, sandbox)
        self.assertEqual((outside / "precious.txt").read_text(), "keep")


class CheckGithubCli(TempDirCase):
    def test_prints_table_and_returns_failure_code(self):
        self.path.write_text("LAB_GH_ORG=org1\nGH_TOKEN=ghu_T\n")
        rows = [("org access", True, "org1"), ("create repo", False, "boom")]
        out = io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(github, "check_s5", return_value=rows) as check, \
             contextlib.redirect_stdout(out):
            rc = tokens.check_github(self.path, self.tmp / "sandbox")
        self.assertEqual(rc, 1)
        self.assertEqual(check.call_args.args[0]["GH_TOKEN"], "ghu_T")
        self.assertIn("org access", out.getvalue())
        self.assertIn("FAIL  boom", out.getvalue())

    def test_missing_token_is_reported(self):
        self.path.write_text("LAB_GH_ORG=org1\n")
        err = io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True), contextlib.redirect_stderr(err):
            rc = tokens.check_github(self.path, self.tmp / "sandbox")
        self.assertEqual(rc, 1)
        self.assertIn("GH_TOKEN is not set", err.getvalue())


class PageShows(unittest.TestCase):
    URL = "https://org1.github.io/lab-x/"

    @staticmethod
    def page(body=b"Hello"):
        resp = mock.MagicMock()
        resp.status = 200
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        return resp

    def fetched(self, url):
        with mock.patch.object(github.urllib.request, "urlopen", return_value=self.page()) as urlopen:
            self.assertTrue(github._page_shows(url, "Hello"))
            self.assertTrue(github._page_shows(url, "Hello"))
        return [call.args[0] for call in urlopen.call_args_list]

    def test_each_check_adds_its_own_cache_busting_query(self):
        first, second = self.fetched(self.URL)
        for fetched in (first, second):
            self.assertTrue(fetched.startswith(self.URL + "?lab-check="), fetched)
        self.assertNotEqual(first, second)

    def test_query_is_appended_to_an_existing_query(self):
        (first, _) = self.fetched(self.URL + "?a=1")
        self.assertTrue(first.startswith(self.URL + "?a=1&lab-check="), first)

    def test_the_site_comparison_ignores_the_query(self):
        key = github._site_key
        self.assertEqual(key(self.URL), "org1.github.io/lab-x")
        self.assertTrue(github._same_site(self.URL, self.URL))


class LabCli(unittest.TestCase):
    @staticmethod
    def load_lab_module():
        # `lab` is also the name of the package directory, so load lab.py by path.
        path = Path(__file__).resolve().parents[2] / "lab.py"
        spec = importlib.util.spec_from_file_location("lab_cli", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_cli(self, *argv):
        cli = self.load_lab_module()
        with mock.patch.object(sys, "argv", ["lab.py", *argv]), \
             mock.patch.object(tokens, "create_github_token", return_value=0) as create, \
             mock.patch.object(tokens, "check_github", return_value=0) as check:
            rc = cli.main()
        return rc, create, check, cli

    def test_tokens_github_without_flag_runs_pat_flow(self):
        rc, create, check, cli = self.run_cli("tokens", "github")
        self.assertEqual(rc, 0)
        create.assert_called_once_with(cli.ENV_FILE)
        check.assert_not_called()

    def test_tokens_github_check_runs_s5_check(self):
        rc, create, check, cli = self.run_cli("tokens", "github", "--check")
        self.assertEqual(rc, 0)
        check.assert_called_once_with(cli.ENV_FILE, cli.SANDBOX)
        create.assert_not_called()

    def test_removed_app_flags_are_rejected(self):
        for flag in ("--register", "--pat"):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.run_cli("tokens", "github", flag)


if __name__ == "__main__":
    unittest.main()
