import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labkit import config, sandbox, statusline  # noqa: E402

STATUSLINE_PY = Path(__file__).resolve().parents[1] / "labkit" / "statusline.py"
CFG = {"GH_TOKEN": "gh-secret", "CLAUDE_CODE_OAUTH_TOKEN": "oauth-secret"}


class TempRoot(unittest.TestCase):
    """A throwaway lab directory; the name contains a space on purpose."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.lab_root = Path(self.dir.name).resolve() / "my lab"
        profile = self.lab_root / "lab" / "profiles" / "clean"
        profile.mkdir(parents=True)
        (profile / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Read"]}}))
        self.test = config.Test("t1", "clean", "prompt-simple.md", "m")
        self.p = sandbox.paths(self.lab_root, "t1")

    def tearDown(self):
        self.dir.cleanup()


class Paths(TempRoot):
    def test_paths_layout_is_resolved(self):
        base = self.lab_root / "sandbox" / "t1"
        self.assertEqual(self.p.base, base)
        self.assertEqual(self.p.config, base / "config")
        self.assertEqual(self.p.work, base)
        self.assertEqual(self.p.repo, base / "xyz")
        self.assertEqual(self.p.results, base / "results")


    def test_paths_refuse_a_base_outside_the_sandbox(self):
        outside = Path(self.dir.name).resolve() / "victim"
        for bad in ("../victim", "../../victim", str(outside), "", ".", "t1/../../victim", "a/b"):
            with self.subTest(test_id=bad), self.assertRaises(ValueError):
                sandbox.paths(self.lab_root, bad)

    def test_paths_refuse_a_symlink_out_of_the_sandbox(self):
        outside = Path(self.dir.name).resolve() / "victim"
        outside.mkdir()
        (self.lab_root / "sandbox").mkdir()
        (self.lab_root / "sandbox" / "t9").symlink_to(outside)
        with self.assertRaises(ValueError):
            sandbox.paths(self.lab_root, "t9")


class OutsideSandbox(TempRoot):
    """Nothing outside <lab_root>/sandbox is ever deleted or written."""

    def setUp(self):
        super().setUp()
        self.victim = Path(self.dir.name).resolve() / "victim"
        (self.victim / "config").mkdir(parents=True)
        (self.victim / "config" / "keep.txt").write_text("keep")

    def test_build_config_refuses_paths_outside_the_sandbox(self):
        p = SimpleNamespace(base=self.victim, config=self.victim / "config", work=self.victim,
                            repo=self.victim / "xyz", results=self.victim / "results")
        with self.assertRaises(ValueError):
            sandbox.build_config(self.lab_root, self.test, p, self.victim / "status.json")
        self.assertEqual((self.victim / "config" / "keep.txt").read_text(), "keep")
        self.assertFalse((self.victim / ".git").exists())

    def test_build_config_refuses_another_tests_paths(self):
        other = sandbox.paths(self.lab_root, "t2")
        other.config.mkdir(parents=True)
        (other.config / "keep.txt").write_text("keep")
        with self.assertRaises(ValueError):
            sandbox.build_config(self.lab_root, self.test, other, other.base / "status.json")
        self.assertTrue((other.config / "keep.txt").exists())

    def test_git_boundary_refuses_a_work_dir_outside_the_sandbox(self):
        with self.assertRaises(ValueError):
            sandbox.write_git_boundary(self.victim, lab_root=self.lab_root)
        self.assertFalse((self.victim / ".git").exists())


class ClearWorkDir(TempRoot):
    """Leftovers of an earlier run in sandbox/<test> are removed before the next one."""

    def setUp(self):
        super().setUp()
        self.outside = Path(self.dir.name).resolve() / "outside"
        self.outside.mkdir()
        (self.outside / "target.txt").write_text("keep")
        w = self.p.work
        for keep in (self.p.config, self.p.results / "20261014T000000Z", self.p.repo):
            keep.mkdir(parents=True)
            (keep / "keep.txt").write_text("keep")
        (w / ".git").write_text("gitdir: /nonexistent\n")
        (w / "poll-pages.sh").write_text("#!/bin/sh\n")
        (w / ".playwright-mcp").mkdir()
        (w / ".playwright-mcp" / "page.yml").write_text("snapshot")
        (w / "link").symlink_to(self.outside)
        (w / "file-link").symlink_to(self.outside / "target.txt")
        self.other = sandbox.paths(self.lab_root, "t2")
        self.other.work.mkdir(parents=True)
        (self.other.work / "poll-pages.sh").write_text("other test")

    def assert_cleared(self, kept=("config", "results/20261014T000000Z", "xyz")):
        names = sorted(e.name for e in self.p.work.iterdir())
        self.assertEqual(names, [".git", "config", "results", "xyz"])
        for keep in (self.p.work / k for k in kept):
            self.assertEqual((keep / "keep.txt").read_text(), "keep")
        self.assertEqual((self.outside / "target.txt").read_text(), "keep")  # symlink removed, not its target
        self.assertEqual((self.other.work / "poll-pages.sh").read_text(), "other test")

    def test_clear_work_dir_keeps_only_the_lab_entries(self):
        sandbox.clear_work_dir(self.lab_root, self.test.id)
        self.assert_cleared()
        self.assertEqual((self.p.work / ".git").read_text(), "gitdir: /nonexistent\n")

    def test_build_config_clears_the_work_dir(self):
        sandbox.build_config(self.lab_root, self.test, self.p, self.p.base / "status.json")
        self.assert_cleared(kept=("results/20261014T000000Z", "xyz"))  # config/ is rebuilt

    def test_missing_work_dir_is_fine(self):
        sandbox.clear_work_dir(self.lab_root, "t3")
        self.assertFalse(sandbox.paths(self.lab_root, "t3").work.exists())

    def test_refuses_a_test_id_outside_the_sandbox(self):
        for bad in ("../outside", str(self.outside)):
            with self.subTest(test_id=bad), self.assertRaises(ValueError):
                sandbox.clear_work_dir(self.lab_root, bad)
        self.assertEqual((self.outside / "target.txt").read_text(), "keep")

    def test_refuses_a_work_dir_that_is_a_symlink_out_of_the_sandbox(self):
        (self.lab_root / "sandbox" / "t9").symlink_to(self.outside)
        with self.assertRaises(ValueError):
            sandbox.clear_work_dir(self.lab_root, "t9")
        self.assertEqual((self.outside / "target.txt").read_text(), "keep")

    def test_invalid_profile_file_deletes_nothing(self):
        (self.lab_root / "lab" / "profiles" / "clean" / "mcp.json").write_text("{not json")
        with self.assertRaises(ValueError):
            sandbox.build_config(self.lab_root, self.test, self.p, self.p.base / "status.json")
        self.assertTrue((self.p.work / "poll-pages.sh").exists())


class ChildEnv(TempRoot):
    def env(self, environ):
        return sandbox.child_env(environ, self.lab_root, self.p.config, CFG)

    def test_child_env_isolation(self):
        env = self.env({"CLAUDECODE": "1", "ANTHROPIC_API_KEY": "x", "CLAUDE_FOO": "y",
                        "GITHUB_TOKEN": "foreign", "HOME": "/h", "PATH": "/bin"})
        for gone in ("CLAUDECODE", "ANTHROPIC_API_KEY", "CLAUDE_FOO", "GITHUB_TOKEN"):
            self.assertNotIn(gone, env)
        self.assertEqual(env["HOME"], "/h")
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["GIT_CEILING_DIRECTORIES"], str(self.lab_root / "sandbox"))
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GH_CONFIG_DIR"], str(self.lab_root / "sandbox" / ".gh"))
        self.assertEqual(env["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(env["GH_TOKEN"], "gh-secret")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.p.config))
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "oauth-secret")

    def test_child_env_opts_out_of_chrome_devtools_statistics(self):
        env = self.env({"PATH": "/bin"})
        self.assertEqual(env["CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS"], "1")
        self.assertEqual(env["CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS"], "1")

    def test_child_env_runs_playwright_mcp_isolated(self):
        # In-memory browser profile: covers the playwright plugin's server, whose args the lab cannot set.
        self.assertEqual(self.env({"PATH": "/bin"})["PLAYWRIGHT_MCP_ISOLATED"], "1")

    def test_child_env_drops_the_ssh_agent(self):
        env = self.env({"SSH_AUTH_SOCK": "/tmp/agent.sock", "SSH_AGENT_PID": "42", "PATH": "/bin"})
        self.assertNotIn("SSH_AUTH_SOCK", env)
        self.assertNotIn("SSH_AGENT_PID", env)
        self.assertEqual(env["PATH"], "/bin")

    def test_lab_repo_invisible_from_work_dir(self):
        """The lab's own git repo must not be found from the sandbox work dir."""
        subprocess.run(["git", "init", "-q", str(self.lab_root)], check=True)
        self.p.repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.p.repo)], check=True)
        env = self.env({"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": "/h"})

        def toplevel(path):
            return subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                                  env=env, capture_output=True, text=True)

        work = toplevel(self.p.work)
        self.assertNotEqual(work.returncode, 0, work.stdout)
        repo = toplevel(self.p.repo)
        self.assertEqual(repo.returncode, 0, repo.stderr)
        self.assertEqual(Path(repo.stdout.strip()).resolve(), self.p.repo)


class GitBoundary(TempRoot):
    """Claude Code ignores GIT_CEILING_DIRECTORIES, so the work dir carries a .git file."""

    HERMETIC = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

    def setUp(self):
        super().setUp()
        subprocess.run(["git", "init", "-q", str(self.lab_root)], check=True, env=self.git_env())
        self.p.repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.p.repo)], check=True, env=self.git_env())

    def git_env(self, extra=None):
        return {**os.environ, **self.HERMETIC, **(extra or {})}

    def toplevel(self, path, env):
        return subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                              env=env, capture_output=True, text=True)

    def test_build_config_writes_git_file(self):
        sandbox.build_config(self.lab_root, self.test, self.p, self.p.base / "status.json")
        git_file = self.p.work / ".git"
        self.assertTrue(git_file.is_file())
        self.assertEqual(git_file.read_text(), "gitdir: /nonexistent\n")

    def test_lab_repo_invisible_with_and_without_ceiling(self):
        sandbox.build_config(self.lab_root, self.test, self.p, self.p.base / "status.json")
        ceiling_env = sandbox.child_env(self.git_env(), self.lab_root, self.p.config, CFG)
        plain_env = self.git_env()
        self.assertNotIn("GIT_CEILING_DIRECTORIES", plain_env)
        for env in (ceiling_env, plain_env):
            cp = self.toplevel(self.p.work, env)
            self.assertNotEqual(cp.returncode, 0, cp.stdout)
            repo = self.toplevel(self.p.repo, env)
            self.assertEqual(repo.returncode, 0, repo.stderr)
            self.assertEqual(Path(repo.stdout.strip()).resolve(), self.p.repo)

    def test_overwrites_existing_git_file(self):
        (self.p.work / ".git").write_text("gitdir: /somewhere\n")
        sandbox.write_git_boundary(self.p.work, lab_root=self.lab_root)
        self.assertEqual((self.p.work / ".git").read_text(), "gitdir: /nonexistent\n")

    def test_refuses_to_clobber_git_directory(self):
        real = self.p.work / ".git"
        real.mkdir()
        (real / "HEAD").write_text("ref: refs/heads/main\n")
        with self.assertRaises(ValueError):
            sandbox.write_git_boundary(self.p.work, lab_root=self.lab_root)
        self.assertTrue(real.is_dir())
        self.assertEqual((real / "HEAD").read_text(), "ref: refs/heads/main\n")


class BuildConfig(TempRoot):
    def build(self):
        status_file = self.p.base / "status.json"
        sandbox.build_config(self.lab_root, self.test, self.p, status_file)
        return status_file

    def test_statusline_command_survives_space_in_path(self):
        status_file = self.build()
        settings = json.loads((self.p.config / "settings.json").read_text())
        command = settings["statusLine"]["command"]
        self.assertEqual(settings["statusLine"]["type"], "command")
        self.assertEqual(shlex.split(command),
                         ["python3", str(STATUSLINE_PY), "--save", str(status_file)])
        self.assertIn(" ", str(status_file))

    def test_settings_keep_profile_and_add_statusline(self):
        self.build()
        settings = json.loads((self.p.config / "settings.json").read_text())
        self.assertEqual(settings["permissions"], {"allow": ["Read"]})
        self.assertIn("statusLine", settings)

    def test_seeded_claude_json_trusts_resolved_work_dir(self):
        self.build()
        state = json.loads((self.p.config / ".claude.json").read_text())
        self.assertIs(state["hasCompletedOnboarding"], True)
        self.assertEqual(state["theme"], "dark")
        self.assertEqual(state["projects"], {str(self.p.work): {
            "hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True}})

    def test_config_dir_is_recreated_empty(self):
        self.p.config.mkdir(parents=True)
        (self.p.config / "stale.txt").write_text("old")
        self.build()
        self.assertEqual(sorted(f.name for f in self.p.config.iterdir()),
                         [".claude.json", "settings.json"])


class ProfileMcpServers(TempRoot):
    """mcp.json servers land in config/.claude.json as user-scope `mcpServers`."""

    def write_mcp(self, text):
        (self.lab_root / "lab" / "profiles" / "clean" / "mcp.json").write_text(text)

    def build(self):
        sandbox.build_config(self.lab_root, self.test, self.p, self.p.base / "status.json")
        return json.loads((self.p.config / ".claude.json").read_text())

    def test_no_mcp_file_no_servers(self):
        self.assertNotIn("mcpServers", self.build())

    def test_servers_written_with_work_dir_replaced(self):
        self.write_mcp(json.dumps({"mcpServers": {
            "filesystem": {"type": "stdio", "command": "npx", "args": ["-y", "fs@1.0.0", "{work}"]},
            "docs": {"type": "http", "url": "https://example.invalid/mcp"},
        }}))
        state = self.build()
        self.assertEqual(state["mcpServers"], {
            "filesystem": {"type": "stdio", "command": "npx", "args": ["-y", "fs@1.0.0", str(self.p.work)]},
            "docs": {"type": "http", "url": "https://example.invalid/mcp"},
        })
        self.assertIn(" ", str(self.p.work))  # one argument, even with a space in the path
        self.assertIn("projects", state)  # the seeded state is kept

    def test_placeholders_replaced_in_args_and_env(self):
        self.write_mcp(json.dumps({"mcpServers": {
            "mem": {
                "type": "stdio", "command": "npx",
                "args": ["--root={work}", "--state={config}/s", 3],
                "env": {"MEMORY_FILE_PATH": "{config}/mcp-memory.jsonl", "WORK": "{work}", "N": 1},
            },
            "plain": {"type": "stdio", "command": "npx", "args": ["-y", "x@1.0.0"]},
        }}))
        servers = self.build()["mcpServers"]
        work, cfg = str(self.p.work), str(self.p.config)
        self.assertEqual(servers["mem"]["args"], [f"--root={work}", f"--state={cfg}/s", 3])
        self.assertEqual(servers["mem"]["env"], {
            "MEMORY_FILE_PATH": f"{cfg}/mcp-memory.jsonl", "WORK": work, "N": 1,
        })
        self.assertNotIn("env", servers["plain"])  # no env in mcp.json: none written

    def test_invalid_mcp_file_stops_before_anything_is_deleted(self):
        self.p.config.mkdir(parents=True)
        (self.p.config / "keep.txt").write_text("keep")
        self.write_mcp("{not json")
        with self.assertRaises(ValueError) as ctx:
            self.build()
        self.assertIn("mcp.json", str(ctx.exception))
        self.assertEqual((self.p.config / "keep.txt").read_text(), "keep")


class PluginVersions(TempRoot):
    def test_missing_file_gives_empty_dict(self):
        self.assertEqual(sandbox.plugin_versions(self.p.config), {})

    def test_reads_installed_plugins(self):
        plugins = self.p.config / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {"superpowers@claude-plugins-official": [{"scope": "user", "version": "5.0.1"}]},
        }))
        self.assertEqual(sandbox.plugin_versions(self.p.config),
                         {"superpowers@claude-plugins-official": "5.0.1"})


class FakeGitHub:
    token = "ghu_fake"

    def __init__(self, exists, create_fails=0):
        self.exists = exists
        self.create_fails = create_fails  # create_repo raises this many times first
        self.calls = []

    def git_env(self):
        return {"GIT_CONFIG_GLOBAL": "/dev/null", "GH_TOKEN": self.token}

    def repo_exists(self, name):
        self.calls.append(("exists", name))
        return self.exists

    def delete_repo(self, name):
        self.calls.append(("delete", name))

    def create_repo(self, name):
        self.calls.append(("create", name))
        if self.create_fails:
            self.create_fails -= 1
            raise sandbox.GitHubError("gh repo create failed: secondary rate limit")
        return f"https://github.com/org/{name}.git"


class ResetRepo(TempRoot):
    def test_recreates_repo_and_wipes_dir(self):
        self.p.repo.mkdir(parents=True)
        (self.p.repo / "old.txt").write_text("old")
        gh = FakeGitHub(exists=True)
        with mock.patch.object(sandbox, "configure_repo") as configure:
            sandbox.reset_repo(gh, "t1", self.p.repo, lab_root=self.lab_root)
        self.assertEqual(gh.calls, [("exists", "lab-t1"), ("delete", "lab-t1"), ("create", "lab-t1")])
        self.assertEqual(list(self.p.repo.iterdir()), [])
        configure.assert_called_once_with(
            self.p.repo, "https://github.com/org/lab-t1.git", gh.git_env(), "ghu_fake"
        )

    def test_skips_delete_when_repo_absent(self):
        gh = FakeGitHub(exists=False)
        with mock.patch.object(sandbox, "configure_repo"):
            sandbox.reset_repo(gh, "t1", self.p.repo, lab_root=self.lab_root)
        self.assertEqual(gh.calls, [("exists", "lab-t1"), ("create", "lab-t1")])
        self.assertTrue(self.p.repo.is_dir())

    def reset_with_pauses(self, gh):
        err = io.StringIO()
        with mock.patch.object(sandbox, "configure_repo"), \
             mock.patch.object(sandbox.time, "sleep") as sleep, redirect_stderr(err):
            sandbox.reset_repo(gh, "t1", self.p.repo, lab_root=self.lab_root)
        return [c.args[0] for c in sleep.call_args_list], err.getvalue()

    def test_retries_when_github_refuses(self):
        gh = FakeGitHub(exists=False, create_fails=2)
        pauses, err = self.reset_with_pauses(gh)
        self.assertEqual(pauses, list(sandbox.RESET_PAUSES_S))
        self.assertEqual([c for c in gh.calls if c[0] == "create"], [("create", "lab-t1")] * 3)
        self.assertIn("secondary rate limit", err)

    def test_gives_up_after_the_last_pause(self):
        gh = FakeGitHub(exists=False, create_fails=len(sandbox.RESET_PAUSES_S) + 1)
        with self.assertRaises(sandbox.GitHubError):
            self.reset_with_pauses(gh)

    def test_a_retry_deletes_a_repo_the_failed_attempt_created(self):
        # GitHub may create the repo and still report an error; the retry starts from scratch.
        gh = FakeGitHub(exists=False, create_fails=1)
        original = gh.create_repo

        def create_then_exist(name):
            gh.exists = True
            return original(name)

        gh.create_repo = create_then_exist
        self.reset_with_pauses(gh)
        self.assertEqual(gh.calls, [("exists", "lab-t1"), ("create", "lab-t1"),
                                    ("exists", "lab-t1"), ("delete", "lab-t1"), ("create", "lab-t1")])

    def assert_refused(self, test_id, repo_dir):
        (repo_dir).mkdir(parents=True, exist_ok=True)
        (repo_dir / "keep.txt").write_text("keep")
        gh = FakeGitHub(exists=True)
        with mock.patch.object(sandbox, "configure_repo") as configure:
            with self.assertRaises(ValueError):
                sandbox.reset_repo(gh, test_id, repo_dir, lab_root=self.lab_root)
        self.assertTrue((repo_dir / "keep.txt").exists())
        self.assertEqual(gh.calls, [])
        configure.assert_not_called()

    def test_refuses_dir_outside_sandbox(self):
        self.assert_refused("t1", Path(self.dir.name).resolve() / "precious")

    def test_refuses_foreign_sandbox_xyz_dir(self):
        """A .../sandbox/x/xyz path outside the lab root must not be wiped."""
        self.assert_refused("x", Path(self.dir.name).resolve() / "other" / "sandbox" / "x" / "xyz")

    def test_refuses_test_id_repo_dir_mismatch(self):
        """Never delete lab-A's remote while wiping test B's checkout."""
        self.assert_refused("t2", self.p.repo)

    def test_refuses_dotdot_escape(self):
        self.assert_refused("t1", self.lab_root / "sandbox" / "t1" / "xyz" / ".." / ".." / ".." / "x" / "xyz")


FAKES = Path(__file__).resolve().parent / "fakes"
PLUGIN = "superpowers@claude-plugins-official"
ADD_MARKETPLACE = [
    "plugin", "marketplace", "add", "anthropics/claude-plugins-official", "--scope", "user", "--json",
]  # fmt: skip
UPDATE_MARKETPLACE = ["plugin", "marketplace", "update", "claude-plugins-official", "--json"]
INSTALL = ["plugin", "install", PLUGIN, "--scope", "user", "--json"]
PERMISSIONS = {"allow": ["Read", "Bash(git:*)"], "deny": ["Bash(cd:*)"]}


class InstallPlugins(TempRoot):
    """install_plugins against the fake `claude`; never installs anything real."""

    def setUp(self):
        super().setUp()
        profile = self.lab_root / "lab" / "profiles" / "sp"
        profile.mkdir()
        (profile / "settings.json").write_text(
            json.dumps({"enabledPlugins": {PLUGIN: True}, "permissions": PERMISSIONS})
        )
        self.status_file = self.p.results / "ts" / "statusline.json"
        sandbox.build_config(self.lab_root, config.Test("t1", "sp", "prompt-simple.md", "m"), self.p, self.status_file)
        self.log = self.p.base / "claude.log"
        environ = {k: v for k, v in os.environ.items() if not k.startswith("FAKE_CLAUDE_")}
        environ.update(PATH=f"{FAKES}{os.pathsep}{os.environ.get('PATH', '')}", FAKE_CLAUDE_LOG=str(self.log))
        self.env = sandbox.child_env(environ, self.lab_root, self.p.config, CFG)

    def install(self, **fake_env):
        return sandbox.install_plugins(self.p.config, [PLUGIN], self.env | fake_env)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def settings(self):
        return json.loads((self.p.config / "settings.json").read_text())

    def test_install_records_versions(self):
        self.assertEqual(self.install(), {PLUGIN: "9.9.9"})
        calls = self.calls()
        self.assertEqual([c["argv"] for c in calls], [ADD_MARKETPLACE, INSTALL])
        for call in calls:
            self.assertEqual(call["config_dir"], str(self.p.config))
            self.assertTrue(call["stdin_devnull"])

    def test_marketplace_added_once_for_several_plugins(self):
        other = "frontend-design@claude-plugins-official"
        versions = sandbox.install_plugins(self.p.config, [PLUGIN, other], self.env)
        self.assertEqual(versions, {PLUGIN: "9.9.9", other: "9.9.9"})
        argvs = [c["argv"] for c in self.calls()]
        self.assertEqual(argvs[0], ADD_MARKETPLACE)
        self.assertEqual([a[:2] for a in argvs[1:]], [["plugin", "install"]] * 2)

    def test_configured_marketplace_is_updated(self):
        known = self.p.config / "plugins" / "known_marketplaces.json"
        known.parent.mkdir()
        known.write_text(json.dumps({"claude-plugins-official": {"source": {}}}))
        self.assertEqual(self.install(), {PLUGIN: "9.9.9"})
        self.assertEqual([c["argv"] for c in self.calls()], [UPDATE_MARKETPLACE, INSTALL])

    def test_add_saying_already_configured_falls_back_to_update(self):
        self.assertEqual(self.install(FAKE_CLAUDE_MARKETPLACE_EXISTS="1"), {PLUGIN: "9.9.9"})
        self.assertEqual([c["argv"] for c in self.calls()], [ADD_MARKETPLACE, UPDATE_MARKETPLACE, INSTALL])

    def test_marketplace_failure_names_it_and_first_error_line(self):
        with self.assertRaises(RuntimeError) as cm:
            self.install(FAKE_CLAUDE_MARKETPLACE_FAIL="1")
        message = str(cm.exception)
        self.assertIn("claude-plugins-official", message)
        self.assertIn("repository not found", message)
        self.assertNotIn("more detail", message)
        self.assertEqual([c["argv"] for c in self.calls()], [ADD_MARKETPLACE])

    def test_unknown_marketplace_stops_before_any_call(self):
        with self.assertRaises(RuntimeError) as cm:
            sandbox.install_plugins(self.p.config, [PLUGIN, "tool@other-market"], self.env)
        self.assertIn("other-market", str(cm.exception))
        self.assertIn("MARKETPLACES", str(cm.exception))
        self.assertEqual(self.calls(), [])

    def test_install_without_marketplace_reproduces_live_error(self):
        """The fake fails like Claude Code did live when no marketplace is configured."""
        with mock.patch.object(sandbox, "_ensure_marketplaces"), self.assertRaises(RuntimeError) as cm:
            self.install()
        live = 'Plugin "superpowers" not found in marketplace "claude-plugins-official"'
        self.assertIn(live, str(cm.exception))

    def test_no_plugins_no_call(self):
        self.assertEqual(sandbox.install_plugins(self.p.config, [], self.env), {})
        self.assertEqual(self.calls(), [])

    def test_failure_names_plugin_and_first_error_line_without_token(self):
        with self.assertRaises(RuntimeError) as cm:
            self.install(FAKE_CLAUDE_PLUGIN_FAIL="leak")
        message = str(cm.exception)
        self.assertIn(PLUGIN, message)
        self.assertIn("not found in any marketplace", message)
        self.assertNotIn("more detail", message)
        self.assertNotIn("oauth-secret", message)
        self.assertNotIn("gh-secret", message)

    def test_marketplace_command_is_left_to_a_human(self):
        with self.assertRaises(RuntimeError) as cm:
            self.install(FAKE_CLAUDE_PLUGIN_COMMAND="1")
        self.assertIn(PLUGIN, str(cm.exception))
        self.assertIn("by hand", str(cm.exception))
        for call in self.calls():
            for flag in ("-y", "--yes", "--accept-command"):
                self.assertNotIn(flag, call["argv"])

    def test_plugin_missing_after_install_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            self.install(FAKE_CLAUDE_PLUGIN_NOOP="1")
        self.assertIn(PLUGIN, str(cm.exception))
        self.assertIn("installed_plugins.json", str(cm.exception))

    def test_settings_restored_after_install_rewrites_them(self):
        before = self.settings()
        err = io.StringIO()
        with redirect_stderr(err):
            self.install(FAKE_CLAUDE_REWRITE_SETTINGS="1")
        after = self.settings()
        self.assertEqual(after["permissions"], PERMISSIONS)
        self.assertEqual(after["statusLine"], before["statusLine"])
        self.assertTrue(after["enabledPlugins"][PLUGIN])
        self.assertIn("restored", err.getvalue())

    def test_settings_untouched_when_install_keeps_them(self):
        before = self.settings()
        self.install()
        self.assertEqual(self.settings(), before)


SPIKE = {
    "context_window": {
        "current_usage": {"input_tokens": 2, "cache_creation_input_tokens": 31113,
                          "cache_read_input_tokens": 0},
        "used_percentage": 3,
        "total_input_tokens": 31115,
        "total_output_tokens": 4,
    },
    "rate_limits": {"five_hour": {"used_percentage": 12}},
}


class StatusLine(unittest.TestCase):
    def test_render_from_spike_fields(self):
        self.assertEqual(statusline.render(SPIKE), "ctx 31.1k (3%) · in 31.1k out 4 · 5h 12% used")

    def test_render_missing_fields(self):
        self.assertEqual(statusline.render({}), "ctx ? (?%) · in ? out ? · 5h ?% used")

    def test_render_empty_current_usage_is_unknown(self):
        data = {"context_window": {"current_usage": {}}}
        self.assertEqual(statusline.render(data), "ctx ? (?%) · in ? out ? · 5h ?% used")

    def test_render_ignores_non_numeric_usage_fields(self):
        usage = {"input_tokens": "x", "cache_creation_input_tokens": 1500, "cache_read_input_tokens": None}
        out = statusline.render({"context_window": {"current_usage": usage}})
        self.assertTrue(out.startswith("ctx 1.5k "), out)
        usage = {"input_tokens": "x"}
        out = statusline.render({"context_window": {"current_usage": usage}})
        self.assertTrue(out.startswith("ctx ? "), out)

    def test_render_survives_non_dict_sections(self):
        for bad in ("oops", 5, ["a"]):
            data = {"context_window": bad, "rate_limits": bad}
            self.assertEqual(statusline.render(data), "ctx ? (?%) · in ? out ? · 5h ?% used")
        data = {"context_window": {"current_usage": "oops"}}
        self.assertEqual(statusline.render(data), "ctx ? (?%) · in ? out ? · 5h ?% used")

    def test_render_small_numbers_stay_exact(self):
        data = {"context_window": {"total_input_tokens": 999, "total_output_tokens": 1000}}
        self.assertEqual(statusline.render(data), "ctx ? (?%) · in 999 out 1.0k · 5h ?% used")

    def test_main_saves_stdin_and_prints_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "status.json"
            target.write_text("stale")
            out = io.StringIO()
            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(SPIKE))), redirect_stdout(out):
                self.assertEqual(statusline.main(["--save", str(target)]), 0)
            self.assertEqual(json.loads(target.read_text()), SPIKE)
            self.assertEqual(out.getvalue().strip(), statusline.render(SPIKE))

    def test_runs_as_script(self):
        cp = subprocess.run([sys.executable, str(STATUSLINE_PY)], input=json.dumps(SPIKE),
                            capture_output=True, text=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(cp.stdout.strip(), statusline.render(SPIKE))


if __name__ == "__main__":
    unittest.main()
