import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labkit import config, github, limits, metrics, results, runner  # noqa: E402

TESTS = Path(__file__).resolve().parent
FAKES = TESTS / "fakes"
FIXTURES = TESTS / "fixtures"
REPO = TESTS.parents[1]
MODEL = "claude-haiku-5-5"
SESSION = "8f01236c-d084-4e20-a838-6504e25a4062"
OAUTH_TOKEN = "sk-ant-oat01-LEAKCHECK"
GH_TOKEN = "ghu_LEAKCHECK"
CFG = {"CLAUDE_CODE_OAUTH_TOKEN": OAUTH_TOKEN, "GH_TOKEN": GH_TOKEN, "LAB_GH_ORG": "lab-org"}
PROMPT = "Build a page that says Hello GitHub Pages.\n"
FOLLOWUPS = ["First follow-up.", "Second follow-up."]
PLUGIN = "superpowers@claude-plugins-official"


class FakeGitHub:
    """Scripted stand-in for labkit.github.GitHub; records every call."""

    token = "ghu_fake"

    def __init__(self, goal_ok=(), pages_status=None, delete_raises=None, goal_ok_raises=None,
                 pages_statuses=None, create_delay=0):
        self.goal_ok_results = list(goal_ok)
        self.status = pages_status
        self.statuses = list(pages_statuses or [])  # one per pages_status call; the last one sticks
        self.delete_raises = delete_raises
        self.goal_ok_raises = goal_ok_raises
        self.create_delay = create_delay
        self.calls = []

    def git_env(self):
        return github.gh_env(os.environ, self.token, Path(os.devnull))

    def repo_exists(self, name):
        self.calls.append(("repo_exists", name))
        return True

    def delete_repo(self, name):
        self.calls.append(("delete_repo", name))
        if self.delete_raises:
            raise self.delete_raises

    def create_repo(self, name):
        self.calls.append(("create_repo", name))
        time.sleep(self.create_delay)
        return f"https://github.com/lab-org/{name}.git"

    def pages_status(self, name):
        self.calls.append(("pages_status", name))
        if self.statuses:
            return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return self.status

    def goal_ok(self, name, text, wait_s):
        self.calls.append(("goal_ok", name, text, wait_s))
        if self.goal_ok_raises:
            raise self.goal_ok_raises
        return self.goal_ok_results.pop(0) if self.goal_ok_results else False


class Gate(unittest.TestCase):
    def gate(self, probe, *, answer=False, is_tty=True):
        asked = []

        def ask(question):
            asked.append(question)
            return answer

        out = io.StringIO()
        with redirect_stdout(out):
            allowed = limits.gate(probe, ask=ask, is_tty=is_tty)
        return allowed, asked, out.getvalue()

    def test_gate_prints_remaining(self):
        allowed, asked, out = self.gate({"five_hour_pct": 12, "seven_day_pct": 5})
        self.assertIn("5-hour limit: 88 % left (7-day: 95 % left)", out)
        self.assertTrue(allowed)
        self.assertEqual(asked, [])

    def test_gate_low_asks(self):
        allowed, asked, out = self.gate({"five_hour_pct": 95, "seven_day_pct": 40}, answer=False)
        self.assertFalse(allowed)
        self.assertEqual(len(asked), 1)
        self.assertIn("Only 5 % of the 5-hour limit left. Run anyway? [y/N]", asked[0])
        self.assertIn("5 % left", out)

    def test_gate_without_tty_stops(self):
        allowed, asked, out = self.gate({"five_hour_pct": 95, "seven_day_pct": 40}, is_tty=False)
        self.assertFalse(allowed)
        self.assertEqual(asked, [])
        self.assertIn("stopping: no terminal to confirm", out)

    def test_gate_unknown_limit_asks(self):
        allowed, asked, out = self.gate(None, answer=True)
        self.assertTrue(allowed)
        self.assertEqual(len(asked), 1)
        self.assertIn("5-hour limit: unknown", out)


class FakeClaude(unittest.TestCase):
    """A throwaway lab root (with a space in its path) and the fake `claude` on PATH."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.lab_root = self.tmp / "my lab"
        profile = self.lab_root / "lab" / "profiles" / "clean"
        profile.mkdir(parents=True)
        (profile / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Read"]}}))
        superpowers = self.lab_root / "lab" / "profiles" / "superpowers"
        superpowers.mkdir()
        (superpowers / "settings.json").write_text(
            json.dumps({"enabledPlugins": {PLUGIN: True}, "permissions": {"allow": ["Read"]}})
        )
        prompts = self.lab_root / "lab" / "prompts"
        prompts.mkdir()
        (prompts / "simple.txt").write_text(PROMPT)
        (prompts / "followups.txt").write_text("# comment\n" + "\n".join(FOLLOWUPS) + "\n")
        self.test = config.Test("test1", "clean", "simple", MODEL)
        self.log = self.tmp / "claude.log"
        environ = {k: v for k, v in os.environ.items() if not k.startswith("FAKE_CLAUDE_")}
        environ.update(
            PATH=f"{FAKES}{os.pathsep}{os.environ.get('PATH', '')}",
            FAKE_CLAUDE_FIXTURE=str(FIXTURES / "headless_subagent"),
            FAKE_CLAUDE_LOG=str(self.log),
        )
        patcher = mock.patch.dict(os.environ, environ, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.out, self.err = io.StringIO(), io.StringIO()

    def run_headless(self, gh, test=None, **kwargs):
        kwargs.setdefault("limit_start", 12)
        with redirect_stdout(self.out), redirect_stderr(self.err):
            return runner.run_headless(self.lab_root, test or self.test, CFG, gh, **kwargs)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def p_calls(self):
        return [c for c in self.calls() if "-p" in c["argv"]]

    def csv_rows(self):
        return results.read(self.lab_root / "results.csv")

    def use_branch_fixture(self, branch):
        """The headless_subagent fixture with `gitBranch` set on every main transcript entry."""
        fixture = self.tmp / "branch-fixture"
        shutil.copytree(FIXTURES / "headless_subagent", fixture)
        (main,) = fixture.glob("projects/*/*.jsonl")
        entries = [json.loads(line) for line in main.read_text().splitlines()]
        main.write_text("".join(json.dumps(e | {"gitBranch": branch}) + "\n" for e in entries))
        os.environ["FAKE_CLAUDE_FIXTURE"] = str(fixture)

    def make_lab_repo(self, branch):
        """Turn the lab root into a git repo with one commit on `branch`."""
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        for args in (["init", "-q", "-b", branch],
                     ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                      "commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(["git", "-C", str(self.lab_root), *args], env=env, check=True, capture_output=True)


class Probe(FakeClaude):
    def test_probe_reads_rate_limit_event(self):
        self.assertEqual(limits.probe(self.lab_root, CFG), {"five_hour_pct": 12, "seven_day_pct": 5})
        (call,) = self.p_calls()
        self.assertEqual(call["argv"][call["argv"].index("-p") + 1], "Reply with OK")

    def test_probe_none_when_call_fails(self):
        os.environ["FAKE_CLAUDE_NO_TRANSCRIPT"] = "1"
        with redirect_stderr(io.StringIO()):
            self.assertIsNone(limits.probe(self.lab_root, CFG))

    def test_probe_failure_prints_the_first_stderr_line_without_tokens(self):
        os.environ["FAKE_CLAUDE_NO_TRANSCRIPT"] = "1"
        os.environ["FAKE_CLAUDE_STDERR"] = f"\nAPI Error: 401 for {OAUTH_TOKEN} and {GH_TOKEN}\nmore detail\n"
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(limits.probe(self.lab_root, CFG))
        self.assertIn("API Error: 401 for *** and ***", err.getvalue())
        self.assertNotIn("LEAKCHECK", err.getvalue())
        self.assertNotIn("more detail", err.getvalue())

    def test_probe_failure_prints_the_result_error_without_stderr(self):
        fixture = self.tmp / "probe-error"
        (fixture / "projects").mkdir(parents=True)
        result = {"type": "result", "is_error": True, "result": "Invalid API key · Please run /login"}
        (fixture / "stream.jsonl").write_text(json.dumps(result) + "\n")
        os.environ.update(FAKE_CLAUDE_FIXTURE=str(fixture), FAKE_CLAUDE_EXIT="1")
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(limits.probe(self.lab_root, CFG))
        self.assertIn("Invalid API key · Please run /login", err.getvalue())

    def test_probe_success_prints_nothing(self):
        err = io.StringIO()
        with redirect_stderr(err):
            limits.probe(self.lab_root, CFG)
        self.assertEqual(err.getvalue(), "")


class HeadlessRun(FakeClaude):
    def test_ok_run_writes_row(self):
        row = self.run_headless(FakeGitHub(goal_ok=[True]))
        self.assertEqual(row["status"], "ok")
        self.assertIs(row["goal_ok"], True)
        self.assertEqual(row["ctx_start"], 18758)
        self.assertEqual(row["limit_5h_start_pct"], 12)
        self.assertEqual(row["limit_5h_end_pct"], 12)
        self.assertEqual(row["mode"], "headless")
        self.assertEqual(row["model"], MODEL)
        self.assertEqual((row["test"], row["profile"], row["prompt"]), ("test1", "clean", "simple"))
        self.assertEqual(set(row), set(results.FIELDS))
        (csv_row,) = self.csv_rows()
        self.assertEqual(csv_row["status"], "ok")
        self.assertEqual(csv_row["ctx_start"], "18758")
        run_dir = self.lab_root / "sandbox" / "test1" / "results" / row["timestamp"]
        self.assertTrue((run_dir / "stream-1.jsonl").is_file())
        self.assertTrue((run_dir / "projects" / "p" / f"{SESSION}.jsonl").is_file())

    def test_first_turn_sends_prompt_unchanged(self):
        self.run_headless(FakeGitHub(goal_ok=[True]))
        (call,) = self.p_calls()
        self.assertEqual(call["argv"][call["argv"].index("-p") + 1], PROMPT)
        self.assertNotIn("--resume", call["argv"])

    def test_followups_until_goal_ok(self):
        gh = FakeGitHub(goal_ok=[False, True])
        row = self.run_headless(gh)
        first, second = self.p_calls()
        argv = second["argv"]
        self.assertEqual(argv[argv.index("--resume") + 1], SESSION)
        self.assertEqual(argv[argv.index("-p") + 1], FOLLOWUPS[0])
        self.assertEqual(row["status"], "ok")
        waits = [c[3] for c in gh.calls if c[0] == "goal_ok"]
        self.assertEqual(waits, [0, 0])

    def test_followups_stop_when_none_left(self):
        gh = FakeGitHub(goal_ok=[])
        row = self.run_headless(gh)
        self.assertEqual(len(self.p_calls()), 1 + len(FOLLOWUPS))
        self.assertEqual(row["status"], "failed")
        self.assertIs(row["goal_ok"], False)
        # Pages was never configured: no 180 s wait for a build that cannot come.
        self.assertEqual([c[3] for c in gh.calls if c[0] == "goal_ok"], [0, 0, 0])

    def test_final_wait_when_pages_appears_after_the_last_turn(self):
        gh = FakeGitHub(goal_ok=[], pages_statuses=[None, None, None, {"status": "building"}])
        self.run_headless(gh)
        # The last check gives a just-configured site the full build time before the verdict.
        self.assertEqual([c[3] for c in gh.calls if c[0] == "goal_ok"], [0, 0, 0, 180])

    def test_no_followup_while_pages_building(self):
        gh = FakeGitHub(goal_ok=[True], pages_status={"status": "building"})
        row = self.run_headless(gh)
        self.assertEqual(len(self.p_calls()), 1)
        self.assertIn(("goal_ok", "lab-test1", "Hello GitHub Pages", 180), gh.calls)
        self.assertEqual(row["status"], "ok")

    def test_timeout_recorded(self):
        os.environ["FAKE_CLAUDE_SLEEP"] = "5"
        row = self.run_headless(FakeGitHub(goal_ok=[True]), timeout_s=1)
        self.assertEqual(row["status"], "timeout")
        self.assertEqual(len(self.p_calls()), 1)
        (csv_row,) = self.csv_rows()
        self.assertEqual(csv_row["status"], "timeout")

    def test_keyboard_interrupt_recorded(self):
        gh = FakeGitHub(delete_raises=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_headless(gh)
        (csv_row,) = self.csv_rows()
        self.assertEqual(csv_row["status"], "failed")
        self.assertEqual(csv_row["test"], "test1")
        self.assertEqual(self.p_calls(), [])

    def test_no_transcript_still_recorded(self):
        os.environ["FAKE_CLAUDE_NO_TRANSCRIPT"] = "1"
        row = self.run_headless(FakeGitHub())
        self.assertEqual(row["status"], "failed")
        (csv_row,) = self.csv_rows()
        self.assertEqual(csv_row["status"], "failed")
        for key in metrics.COMPUTE_KEYS:
            self.assertEqual(csv_row[key], "", key)

    def test_denied_when_pages_fail_and_denials(self):
        fixture = self.tmp / "denied"
        shutil.copytree(FIXTURES / "headless_subagent", fixture)
        stream = fixture / "stream.jsonl"
        events = [json.loads(line) for line in stream.read_text().splitlines()]
        for event in events:
            if event.get("type") == "result":
                event["permission_denials"] = [{"tool_name": "Bash"}]
        stream.write_text("".join(json.dumps(e) + "\n" for e in events))
        os.environ["FAKE_CLAUDE_FIXTURE"] = str(fixture)
        self.assertEqual(self.run_headless(FakeGitHub())["status"], "denied")

    def test_warns_when_run_sees_lab_repo(self):
        self.make_lab_repo("feature/x")
        self.use_branch_fixture("feature/x")
        row = self.run_headless(FakeGitHub(goal_ok=[True]))
        self.assertIn(
            "warning: test1 saw the lab repo (gitBranch feature/x)"
            " — token counts include lab git context",
            self.err.getvalue(),
        )
        self.assertEqual(row["status"], "ok")

    def test_no_warning_with_git_boundary(self):
        self.make_lab_repo("feature/x")
        self.use_branch_fixture("HEAD")
        self.run_headless(FakeGitHub(goal_ok=[True]))
        self.assertNotIn("warning", self.err.getvalue())

    def test_no_warning_when_lab_repo_detached(self):
        self.make_lab_repo("feature/x")
        subprocess.run(["git", "-C", str(self.lab_root), "checkout", "-q", "--detach"],
                       check=True, capture_output=True)
        self.use_branch_fixture("HEAD")
        self.run_headless(FakeGitHub(goal_ok=[True]))
        self.assertNotIn("warning", self.err.getvalue())

    def test_no_warning_outside_a_git_repo(self):
        self.use_branch_fixture("feature/x")
        row = self.run_headless(FakeGitHub(goal_ok=[True]))
        self.assertNotIn("warning", self.err.getvalue())
        self.assertEqual(row["status"], "ok")

    def test_claude_stderr_kept_per_turn(self):
        os.environ["FAKE_CLAUDE_STDERR"] = "API Error: 401 authentication failed\n"
        os.environ["FAKE_CLAUDE_EXIT"] = "3"
        row = self.run_headless(FakeGitHub(goal_ok=[False, True]))
        run_dir = Path("sandbox") / "test1" / "results" / row["timestamp"]
        for n in (1, 2):
            text = (self.lab_root / run_dir / f"stderr-{n}.txt").read_text()
            self.assertEqual(text, "API Error: 401 authentication failed\n")
            for token in (OAUTH_TOKEN, GH_TOKEN, "LEAKCHECK"):
                self.assertNotIn(token, text)
            self.assertIn(f"test1: claude exited 3 (see {run_dir / f'stderr-{n}.txt'})",
                          self.err.getvalue())
        self.assertNotIn("API Error", self.out.getvalue() + self.err.getvalue())

    def test_no_exit_line_on_success(self):
        self.run_headless(FakeGitHub(goal_ok=[True]))
        self.assertNotIn("claude exited", self.out.getvalue() + self.err.getvalue())

    def test_plugins_installed_before_first_turn(self):
        test = config.Test("test3", "superpowers", "simple", MODEL)
        row = self.run_headless(FakeGitHub(goal_ok=[True]), test)
        marketplace, install, turn = self.calls()
        self.assertEqual(marketplace["argv"][:3], ["plugin", "marketplace", "add"])
        self.assertEqual(install["argv"], ["plugin", "install", PLUGIN, "--scope", "user", "--json"])
        for call in (marketplace, install):
            self.assertEqual(call["config_dir"], str(self.lab_root / "sandbox" / "test3" / "config"))
            self.assertTrue(call["stdin_devnull"])
        self.assertIn("-p", turn["argv"])
        self.assertEqual(row["plugin_versions"], {PLUGIN: "9.9.9"})
        (csv_row,) = self.csv_rows()
        self.assertEqual(csv_row["plugin_versions"], f"{PLUGIN}@9.9.9")
        self.assertIn(f"test3: installed {PLUGIN} 9.9.9", self.out.getvalue())

    def test_clean_profile_installs_nothing(self):
        self.run_headless(FakeGitHub(goal_ok=[True]))
        self.assertEqual([c for c in self.calls() if c["argv"][:1] == ["plugin"]], [])

    def test_install_failure_recorded(self):
        os.environ["FAKE_CLAUDE_PLUGIN_FAIL"] = "1"
        test = config.Test("test3", "superpowers", "simple", MODEL)
        with self.assertRaises(RuntimeError):
            self.run_headless(FakeGitHub(goal_ok=[True]), test)
        (csv_row,) = self.csv_rows()
        self.assertEqual(csv_row["status"], "failed")
        self.assertEqual(self.p_calls(), [])

    def test_install_time_not_in_run_budget(self):
        os.environ["FAKE_CLAUDE_PLUGIN_SLEEP"] = "1.5"
        test = config.Test("test3", "superpowers", "simple", MODEL)
        row = self.run_headless(FakeGitHub(goal_ok=[True]), test, timeout_s=1)
        self.assertEqual(row["status"], "ok")
        self.assertLessEqual(row["duration_s"], 1)

    def test_repo_reset_not_in_run_budget_or_duration(self):
        row = self.run_headless(FakeGitHub(goal_ok=[True], create_delay=1.5), timeout_s=1)
        self.assertEqual(row["status"], "ok")
        self.assertLessEqual(row["duration_s"], 1)

    def test_warns_when_transcript_and_stream_totals_differ(self):
        fixture = self.tmp / "totals"
        shutil.copytree(FIXTURES / "headless_subagent", fixture)
        stream = fixture / "stream.jsonl"
        events = [json.loads(line) for line in stream.read_text().splitlines()]
        for event in events:
            for usage in (event.get("modelUsage") or {}).values():
                usage["outputTokens"] += 1
        stream.write_text("".join(json.dumps(e) + "\n" for e in events))
        os.environ["FAKE_CLAUDE_FIXTURE"] = str(fixture)
        row = self.run_headless(FakeGitHub(goal_ok=[True]))
        self.assertIn("warning: test1: token totals from the transcripts differ", self.err.getvalue())
        self.assertIn("tokens_out 880 vs 881", self.err.getvalue())
        self.assertEqual(row["tokens_out"], 880)  # the row keeps the transcript totals

    def test_matching_totals_print_no_warning(self):
        self.run_headless(FakeGitHub(goal_ok=[True]))
        self.assertNotIn("differ", self.err.getvalue())

    def start_grandchild(self):
        pid_file = self.tmp / "grandchild.pid"
        os.environ.update(FAKE_CLAUDE_GRANDCHILD=str(pid_file), FAKE_CLAUDE_SLEEP="30")
        self.addCleanup(self.kill_quietly, pid_file)
        return pid_file

    @staticmethod
    def kill_quietly(pid_file):
        try:
            os.kill(int(pid_file.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass

    def assert_gone(self, pid_file):
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail(f"grandchild {pid} outlived the run")

    def test_timeout_kills_the_whole_process_group(self):
        pid_file = self.start_grandchild()
        row = self.run_headless(FakeGitHub(goal_ok=[True]), timeout_s=2)
        self.assertEqual(row["status"], "timeout")
        self.assert_gone(pid_file)

    def test_interrupt_kills_the_whole_process_group(self):
        pid_file = self.start_grandchild()

        def interrupt_once_claude_runs():
            deadline = time.monotonic() + 10
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            os.kill(os.getpid(), signal.SIGINT)

        threading.Thread(target=interrupt_once_claude_runs, daemon=True).start()
        with self.assertRaises(KeyboardInterrupt):
            self.run_headless(FakeGitHub(goal_ok=[True]))
        self.assert_gone(pid_file)
        (csv_row,) = self.csv_rows()
        self.assertEqual(csv_row["status"], "failed")

    def test_token_never_in_argv_or_csv(self):
        limits.probe(self.lab_root, CFG)
        self.run_headless(FakeGitHub(goal_ok=[False, True]))
        calls = self.calls()
        self.assertTrue(calls and all(c["oauth_env"] for c in calls))
        texts = {
            "claude log": self.log.read_text(),
            "results.csv": (self.lab_root / "results.csv").read_text(),
            "stdout": self.out.getvalue(),
            "stderr": self.err.getvalue(),
        }
        for where, text in texts.items():
            for token in (OAUTH_TOKEN, GH_TOKEN, "LEAKCHECK"):
                self.assertNotIn(token, text, where)

    def test_flags_pinned(self):
        limits.probe(self.lab_root, CFG)
        self.run_headless(FakeGitHub(goal_ok=[False, False]))
        calls = self.p_calls()
        self.assertEqual(len(calls), 1 + 1 + len(FOLLOWUPS))  # probe + turn 1 + follow-ups
        for call in calls:
            argv = call["argv"]
            self.assertEqual(argv[argv.index("--model") + 1], MODEL)
            self.assertEqual(argv[argv.index("--permission-mode") + 1], "default")
            self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
            self.assertIn("--verbose", argv)
            self.assertNotIn("--bare", argv)
            self.assertNotIn("bypassPermissions", argv)
            self.assertTrue(call["stdin_devnull"], argv)
        for call in calls[1:]:
            self.assertEqual(call["cwd"], str(self.lab_root / "sandbox" / "test1"))


class Interactive(FakeClaude):
    """run_interactive with the fake `claude` as the session; the terminal is inherited."""

    def setUp(self):
        super().setUp()
        os.environ["FAKE_CLAUDE_FIXTURE"] = str(FIXTURES / "interactive_single")

    def run_interactive(self, gh, test=None, **kwargs):
        kwargs.setdefault("limit_start", 12)
        with redirect_stdout(self.out), redirect_stderr(self.err):
            return runner.run_interactive(self.lab_root, test or self.test, CFG, gh, **kwargs)

    def sessions(self):
        return [c for c in self.calls() if c["argv"][:1] != ["plugin"]]

    def test_interactive_row_from_transcript_and_statusline(self):
        gh = FakeGitHub(goal_ok=[True], pages_status={"status": "building"})
        row = self.run_interactive(gh)
        self.assertEqual(row["ctx_start"], 31115)
        self.assertEqual(row["limit_5h_end_pct"], 13)
        self.assertEqual(row["limit_5h_start_pct"], 12)
        self.assertEqual(row["mode"], "interactive")
        self.assertEqual(row["status"], "ok")
        self.assertIs(row["goal_ok"], True)
        self.assertEqual(set(row), set(results.FIELDS))
        self.assertIn(("goal_ok", "lab-test1", "Hello GitHub Pages", 180), gh.calls)
        (csv_row,) = self.csv_rows()
        self.assertEqual((csv_row["mode"], csv_row["ctx_start"]), ("interactive", "31115"))
        run_dir = self.lab_root / "sandbox" / "test1" / "results" / row["timestamp"]
        self.assertTrue((run_dir / "statusline.json").is_file())
        self.assertTrue(any((run_dir / "projects").glob("*/*.jsonl")))

    def test_no_pages_wait_when_pages_was_never_configured(self):
        gh = FakeGitHub(goal_ok=[False])
        row = self.run_interactive(gh)
        self.assertEqual([c[3] for c in gh.calls if c[0] == "goal_ok"], [0])
        self.assertNotIn("waiting up to", self.out.getvalue())
        self.assertEqual(row["status"], "failed")

    def test_session_argv_and_environment(self):
        self.run_interactive(FakeGitHub(goal_ok=[True]))
        (session,) = self.sessions()
        self.assertEqual(session["argv"], [PROMPT, "--model", MODEL, "--permission-mode", "default"])
        self.assertNotIn("-p", session["argv"])
        self.assertEqual(session["cwd"], str(self.lab_root / "sandbox" / "test1"))
        self.assertEqual(session["config_dir"], str(self.lab_root / "sandbox" / "test1" / "config"))
        self.assertTrue(session["oauth_env"])

    def test_goal_not_met_is_failed(self):
        row = self.run_interactive(FakeGitHub(goal_ok=[False]))
        self.assertEqual(row["status"], "failed")
        self.assertIs(row["goal_ok"], False)

    def test_no_prompt_writes_no_row(self):
        gh = FakeGitHub(goal_ok=[True])
        self.assertIsNone(self.run_interactive(gh, prompt=False))
        self.assertFalse((self.lab_root / "results.csv").exists())
        (session,) = self.sessions()
        self.assertEqual(session["argv"], ["--model", MODEL, "--permission-mode", "default"])
        self.assertNotIn("goal_ok", [c[0] for c in gh.calls])
        self.assertIn(str(Path("sandbox") / "test1" / "config"), self.out.getvalue())

    def test_interactive_installs_profile_plugins(self):
        test = config.Test("test3", "superpowers", "simple", MODEL)
        row = self.run_interactive(FakeGitHub(goal_ok=[True]), test)
        argvs = [c["argv"] for c in self.calls()]
        self.assertEqual(argvs[0][:3], ["plugin", "marketplace", "add"])
        self.assertEqual(argvs[1][:3], ["plugin", "install", PLUGIN])
        self.assertEqual(argvs[2][0], PROMPT)
        self.assertEqual(row["plugin_versions"], {PLUGIN: "9.9.9"})

    def test_interrupt_during_pages_wait_recorded(self):
        gh = FakeGitHub(goal_ok_raises=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_interactive(gh)
        (csv_row,) = self.csv_rows()
        self.assertEqual(csv_row["status"], "failed")
        self.assertEqual(csv_row["ctx_start"], "31115")

    def test_ctrl_c_in_session_does_not_end_the_run(self):
        os.environ["FAKE_CLAUDE_SIGINT_PARENT"] = "1"
        try:
            row = self.run_interactive(FakeGitHub(goal_ok=[True]))
        except KeyboardInterrupt:
            self.fail("Ctrl+C inside the session interrupted the lab")
        self.assertEqual(row["status"], "ok")

    def test_session_without_transcript_or_statusline(self):
        os.environ["FAKE_CLAUDE_NO_TRANSCRIPT"] = "1"
        row = self.run_interactive(FakeGitHub(goal_ok=[False]))
        self.assertEqual(row["status"], "failed")
        self.assertIsNone(row["limit_5h_end_pct"])
        self.assertIsNone(row["ctx_start"])


def load_cli():
    # `lab` is also the name of the package directory, so load lab.py by path.
    spec = importlib.util.spec_from_file_location("lab_cli", REPO / "lab.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


class RunCommand(unittest.TestCase):
    """`lab.py run` with the probe and the runner replaced; nothing is called for real."""

    def run_tests(self, cfg, probe, selection="test1", repeat=None):
        cli = load_cli()
        row = dict.fromkeys(results.FIELDS) | {"status": "ok"}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.limits, "probe", return_value=probe), \
             mock.patch.object(cli.runner, "run_headless", return_value=row) as run, \
             mock.patch.object(sys.stdin, "isatty", return_value=False), \
             redirect_stdout(out), redirect_stderr(err):
            code = cli.run_tests(CFG | cfg, selection, repeat)
        return code, run, out.getvalue(), err.getvalue()

    def test_run_skips_test_when_gate_says_no(self):
        code, run, out, _ = self.run_tests({}, {"five_hour_pct": 95, "seven_day_pct": 1})
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertIn("stopping: no terminal to confirm", out)
        self.assertIn("skipping test1", out)

    def test_run_uses_repeat_and_model_from_config(self):
        cfg = {"REPEAT": "2", "LAB_MODEL": "claude-sonnet-x"}
        code, run, out, _ = self.run_tests(cfg, {"five_hour_pct": 12, "seven_day_pct": 5})
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertEqual(call.args[1].model, "claude-sonnet-x")
            self.assertEqual(call.kwargs["limit_start"], 12)
        self.assertEqual(out.count("88 % left"), 2)

    def test_run_stops_on_runtime_error(self):
        cli = load_cli()
        err = io.StringIO()
        with mock.patch.object(cli.limits, "probe", return_value={"five_hour_pct": 1, "seven_day_pct": 1}), \
             mock.patch.object(cli.runner, "run_headless", side_effect=RuntimeError("install failed")), \
             mock.patch.object(sys.stdin, "isatty", return_value=False), \
             redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(cli.run_tests(CFG, "all", 1), 1)
        self.assertIn("error: install failed", err.getvalue())

    def test_run_rejects_bad_repeat_and_unknown_test(self):
        code, run, _, err = self.run_tests({"REPEAT": "0"}, None)
        self.assertEqual(code, 1)
        self.assertIn("REPEAT", err)
        code, run, _, err = self.run_tests({}, None, selection="nope")
        self.assertEqual(code, 1)
        self.assertIn("unknown test 'nope'", err)
        run.assert_not_called()


class ResultsHeader(unittest.TestCase):
    """`run` and `session` refuse an old results.csv before any tokens are spent."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.csv = Path(tmp.name) / "results.csv"
        self.cli = load_cli()
        self.cli.LAB_ROOT = Path(tmp.name)
        patches = (
            mock.patch.object(self.cli.env, "load", return_value=dict(CFG)),
            mock.patch.object(self.cli.env, "file_problems", return_value=[]),
            mock.patch.object(self.cli.preflight, "check", return_value=([], [])),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def main(self, *argv):
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["lab.py", *argv]), \
             mock.patch.object(self.cli, "run_tests", return_value=0) as run, \
             mock.patch.object(self.cli, "run_session", return_value=0) as session, \
             redirect_stdout(io.StringIO()), redirect_stderr(err):
            try:
                code = self.cli.main()
            except SystemExit as exit_:  # prepare() exits with 1 on errors
                code = exit_.code
        return code, run, session, err.getvalue()

    def test_old_header_stops_run_and_session_with_the_move_aside_hint(self):
        self.csv.write_text("timestamp,test,pages_ok\n20261001T000000Z,test1,True\n")
        for argv in (("run", "test1"), ("session", "test1")):
            with self.subTest(argv=argv):
                code, run, session, err = self.main(*argv)
                self.assertEqual(code, 1)
                run.assert_not_called()
                session.assert_not_called()
                self.assertIn("error:", err)
                self.assertIn(str(self.csv), err)
                self.assertIn("move it aside", err)

    def test_current_header_missing_file_and_empty_file_pass(self):
        for content in (None, "", ",".join(results.FIELDS) + "\n"):
            with self.subTest(content=content):
                self.csv.unlink(missing_ok=True)
                if content is not None:
                    self.csv.write_text(content)
                code, run, _, _ = self.main("run", "test1")
                self.assertEqual(code, 0)
                run.assert_called_once()

    def test_report_and_teardown_do_not_check_the_header(self):
        self.csv.write_text("timestamp,test,pages_ok\n")
        with mock.patch.object(self.cli, "run_teardown", return_value=0) as teardown:
            with mock.patch.object(sys, "argv", ["lab.py", "teardown"]):
                self.assertEqual(self.cli.main(), 0)
        teardown.assert_called_once()


class SessionCommand(unittest.TestCase):
    """`lab.py session` with the probe and the runner replaced; nothing is called for real."""

    def session(self, test_id, probe, *, prompt=True, row=None):
        cli = load_cli()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.limits, "probe", return_value=probe), \
             mock.patch.object(cli.runner, "run_interactive", return_value=row) as run, \
             mock.patch.object(sys.stdin, "isatty", return_value=False), \
             redirect_stdout(out), redirect_stderr(err):
            code = cli.run_session(CFG | {"LAB_MODEL": "claude-sonnet-x"}, test_id, prompt)
        return code, run, out.getvalue(), err.getvalue()

    def test_session_gates_then_runs(self):
        row = dict.fromkeys(results.FIELDS) | {"test": "test1", "status": "ok", "ctx_start": 31115}
        code, run, out, _ = self.session("test1", {"five_hour_pct": 12, "seven_day_pct": 5}, row=row)
        self.assertEqual(code, 0)
        self.assertIn("88 % left", out)
        (call,) = run.call_args_list
        self.assertEqual(call.args[1].model, "claude-sonnet-x")
        self.assertEqual(call.kwargs, {"limit_start": 12, "prompt": True})
        self.assertIn("test1: ok · ctx_start 31115", out)

    def test_session_no_prompt_passes_through_and_prints_no_summary(self):
        code, run, out, _ = self.session("test1", {"five_hour_pct": 12, "seven_day_pct": 5}, prompt=False)
        self.assertEqual(code, 0)
        self.assertFalse(run.call_args.kwargs["prompt"])
        self.assertNotIn("ctx_start", out)

    def test_session_stops_when_gate_says_no(self):
        code, run, out, _ = self.session("test1", {"five_hour_pct": 95, "seven_day_pct": 5})
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertIn("stopping: no terminal to confirm", out)

    def test_session_unknown_test(self):
        code, run, _, err = self.session("nope", None)
        self.assertEqual(code, 1)
        self.assertIn("unknown test 'nope'", err)
        run.assert_not_called()


class ReportCommand(unittest.TestCase):
    def report(self, text):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.csv"
            path.write_text(text)
            out, err = io.StringIO(), io.StringIO()
            cli.LAB_ROOT = Path(tmp)
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.run_report()
        return code, out.getvalue(), err.getvalue()

    def test_warns_in_one_line_when_the_file_lacks_goal_ok(self):
        old = "timestamp,test,mode,model,profile,prompt,status,pages_ok\n20261001T000000Z,test1,headless,m,clean,simple,ok,True\n"
        _, out, err = self.report(old)
        warnings = [line for line in (out + err).splitlines() if line.startswith("warning:")]
        self.assertEqual(len(warnings), 1)
        self.assertIn("goal_ok", warnings[0])

    def test_no_warning_for_a_current_file(self):
        header = ",".join(results.FIELDS)
        _, out, err = self.report(header + "\n" + ",".join("" for _ in results.FIELDS) + "\n")
        self.assertNotIn("warning:", out + err)


class RepeatOption(unittest.TestCase):
    def test_repeat_must_be_positive_integer(self):
        cli = load_cli()
        self.assertEqual(cli.positive_int("3"), 3)
        for bad in ("0", "-1", "two", "", "1.5"):
            with self.assertRaises(ValueError):
                cli.positive_int(bad)


if __name__ == "__main__":
    unittest.main()
