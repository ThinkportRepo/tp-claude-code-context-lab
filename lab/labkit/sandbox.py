"""Per-run sandbox: paths, child environment, Claude config dir, test repo.

Layout under `<lab_root>/sandbox/<test>/`: `config/` is Claude's fresh config
dir, the directory itself is Claude's cwd, `xyz/` is the checked-out test
repo and `results/` holds the run's output. Makes no network calls except
through the `GitHub` object it is given.
"""

import json
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

from . import config
from .github import GitHub, GitHubError, configure_repo, gh_env

STATUSLINE_PY = Path(__file__).resolve().with_name("statusline.py")
REPO_DIRNAME = "xyz"
# Pauses before retrying a repo reset GitHub refused (e.g. a secondary rate
# limit on creating repos); the run's clock has not started yet.
RESET_PAUSES_S = (30, 90)


def paths(lab_root: Path, test_id: str) -> SimpleNamespace:
    """The test's directories, resolved; ValueError unless the base lies directly in <lab_root>/sandbox.

    Runs wipe and rewrite these directories, so an id like `../x`, an
    absolute path or a symlink out of the sandbox is refused here.
    """
    sandbox_dir = (lab_root / "sandbox").resolve()
    base = (sandbox_dir / test_id).resolve()
    if base.parent != sandbox_dir:
        raise ValueError(f"refusing test id {test_id!r}: {base} is not a directory in {sandbox_dir}")
    return SimpleNamespace(
        base=base,
        config=base / "config",
        work=base,
        repo=base / REPO_DIRNAME,
        results=base / "results",
    )


def child_env(
    environ: Mapping[str, str], lab_root: Path, config_dir: Path, cfg: dict[str, str]
) -> dict[str, str]:
    """Environment for the `claude` child process.

    Drops every CLAUDE*/ANTHROPIC* variable so no outside login or setting
    leaks in, then pins the lab's credentials and stops git from walking up
    into the lab's own repository.

    It also opts chrome-devtools-mcp out of its usage statistics (sent to
    Google by default) and its update checks. MCP servers inherit Claude
    Code's environment, so this covers a user-scope server and a plugin's
    server alike: no run phones home, and no network call outside the task
    adds noise to the measurement.

    `PLAYWRIGHT_MCP_ISOLATED=1` is the environment form of `@playwright/mcp`'s
    `--isolated`: an in-memory browser profile instead of a persistent one
    under the user's home directory, keyed by the work dir. Without it, the
    `playwright` plugin's server (whose args the lab cannot set) would carry
    browser cache, cookies and history from one run of a test to the next.
    """
    sandbox_dir = (lab_root / "sandbox").resolve()
    env = gh_env(environ, cfg["GH_TOKEN"], sandbox_dir / ".gh")  # drops CLAUDE*/ANTHROPIC* too
    env.update(
        CLAUDE_CONFIG_DIR=str(config_dir),
        CLAUDE_CODE_OAUTH_TOKEN=cfg["CLAUDE_CODE_OAUTH_TOKEN"],
        GIT_CEILING_DIRECTORIES=str(sandbox_dir),
        CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS="1",
        CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS="1",
        PLAYWRIGHT_MCP_ISOLATED="1",
    )
    return env


def write_git_boundary(work_dir: Path, *, lab_root: Path) -> None:
    """Make `work_dir` look like a (broken) git repo so no parent repo is found.

    Claude Code discovers parent repositories on its own and ignores
    GIT_CEILING_DIRECTORIES (observed on Claude Code 2.1.296, undocumented).
    Without this, a run in sandbox/<test> reports the lab repo's branch,
    recent commits and memory path. A `.git` file pointing nowhere stops the
    search at `work_dir` with a neutral state, while the nested `xyz/` repo
    still works. A real `.git` directory is never overwritten, and nothing
    is written outside a test directory in `<lab_root>/sandbox`.
    """
    sandbox_dir = (lab_root / "sandbox").resolve()
    if work_dir.resolve().parent != sandbox_dir:
        raise ValueError(f"refusing to write {work_dir / '.git'}: not a test directory in {sandbox_dir}")
    git_path = work_dir / ".git"
    if git_path.is_dir() and not git_path.is_symlink():
        raise ValueError(f"refusing to replace the git directory {git_path}")
    git_path.unlink(missing_ok=True)
    git_path.write_text("gitdir: /nonexistent\n")


# What the lab itself keeps in sandbox/<test>; everything else there is a run's leftover.
WORK_DIR_KEEP = frozenset({"config", "results", REPO_DIRNAME, ".git"})


def clear_work_dir(lab_root: Path, test_id: str) -> None:
    """Remove every entry directly in sandbox/<test_id> except WORK_DIR_KEEP.

    Claude runs in that directory, and files it writes there outside `xyz/`
    (scripts, `.playwright-mcp/` snapshots) would otherwise be there for the
    next run. The directory comes from `paths`, so an id that leads outside
    `<lab_root>/sandbox` is refused. A symlink is removed itself, never its
    target, and `rmtree` does not follow symlinks inside a directory.
    """
    work = paths(lab_root, test_id).work
    if not work.is_dir():
        return
    for entry in work.iterdir():
        if entry.name in WORK_DIR_KEEP:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _with_placeholders(server: dict, values: dict[str, str]) -> dict:
    """A copy of an MCP server config with placeholders filled in.

    Each `{name}` from `values` is replaced in the string items of `args`
    and in the string values of `env`; nothing else is touched, and a server
    without `args` or `env` gets none.
    """

    def fill(value):
        if not isinstance(value, str):
            return value
        for name, replacement in values.items():
            value = value.replace("{" + name + "}", replacement)
        return value

    out = dict(server)
    if isinstance(server.get("args"), list):
        out["args"] = [fill(a) for a in server["args"]]
    if isinstance(server.get("env"), dict):
        out["env"] = {key: fill(v) for key, v in server["env"].items()}
    return out


def build_config(lab_root: Path, test: config.Test, p: SimpleNamespace, status_file: Path) -> None:
    """Recreate p.config with the profile's settings, a status line and a trusted cwd.

    Also removes an earlier run's leftovers from the work dir
    (`clear_work_dir`), so every run starts with the same files.

    The profile's mcp.json servers become user-scope `mcpServers` in
    `.claude.json`. In their args and env values, `{work}` becomes the work
    dir and `{config}` this fresh config dir, so a server can keep its state
    in a place that every run recreates.
    `p` must be `paths(lab_root, test.id)`, and the profile files must
    parse; otherwise this raises before anything is deleted.
    """
    expected = paths(lab_root, test.id)
    if (p.config.resolve(), p.work.resolve()) != (expected.config, expected.work):
        raise ValueError(f"refusing to rebuild {p.config}: not the config dir of test {test.id!r}")
    settings = config.profile_settings(lab_root, test.profile)
    servers = config.profile_mcp_servers(lab_root, test.profile)
    clear_work_dir(lab_root, test.id)
    shutil.rmtree(p.config, ignore_errors=True)
    p.config.mkdir(parents=True)
    settings["statusLine"] = {
        "type": "command",
        "command": f"python3 {shlex.quote(str(STATUSLINE_PY))} --save {shlex.quote(str(status_file))}",
    }
    (p.config / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    state = {
        "hasCompletedOnboarding": True,
        "theme": "dark",
        "projects": {
            str(p.work.resolve()): {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True}
        },
    }
    if servers:
        values = {"work": str(p.work.resolve()), "config": str(p.config.resolve())}
        state["mcpServers"] = {name: _with_placeholders(server, values) for name, server in servers.items()}
    (p.config / ".claude.json").write_text(json.dumps(state, indent=2) + "\n")
    write_git_boundary(p.work, lab_root=lab_root)


def plugin_versions(config_dir: Path) -> dict[str, str]:
    """Installed plugin -> version, as recorded by Claude Code; {} if none."""
    path = config_dir / "plugins" / "installed_plugins.json"
    if not path.is_file():
        return {}
    versions: dict[str, str] = {}
    for name, entries in json.loads(path.read_text()).get("plugins", {}).items():
        entry = entries[0] if isinstance(entries, list) and entries else entries
        versions[name] = str(entry.get("version", "?")) if isinstance(entry, dict) else "?"
    return versions


class PluginInstallError(RuntimeError):
    """A profile plugin could not be installed; the message never contains a token."""


# Marketplace name (the part after `@` in a plugin key) -> source for
# `claude plugin marketplace add`. A fresh config dir knows no marketplace,
# and `plugin install` does not fetch one on its own.
MARKETPLACES = {"claude-plugins-official": "anthropics/claude-plugins-official"}


def profile_plugins(lab_root: Path, profile: str) -> list[str]:
    """The plugin keys (`name@marketplace`) a profile enables, in file order."""
    enabled = config.profile_settings(lab_root, profile).get("enabledPlugins") or {}
    return [key for key, on in enabled.items() if on]


def first_line(text: str, secrets: list[str]) -> str:
    """The first non-blank line of text, secrets replaced by ***, at most 300 chars."""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    for secret in secrets:
        line = line.replace(secret, "***")
    return line[:300]


def _read_settings(path: Path) -> dict:
    try:
        settings = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def _restore_settings(path: Path, before: dict) -> list[str]:
    """Put back what an install changed or dropped in settings.json.

    The profile's and the lab's keys win; `enabledPlugins` is merged, so
    entries the installer added stay. Returns the restored keys.
    """
    after = _read_settings(path)
    restored = []
    for key, value in before.items():
        if key == "enabledPlugins" and isinstance(value, dict):
            current = after.get(key) if isinstance(after.get(key), dict) else {}
            value = current | value
        if after.get(key) != value:
            after[key] = value
            restored.append(key)
    if restored:
        path.write_text(json.dumps(after, indent=2) + "\n")
    return restored


def _plugin_cli(
    args: list[str], config_dir: Path, env: dict[str, str], timeout_s: int, what: str
) -> subprocess.CompletedProcess:
    """Run `claude plugin <args>` in the run's environment; stdin is /dev/null."""
    try:
        return subprocess.run(
            ["claude", "plugin", *args],
            cwd=config_dir, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout_s,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        raise PluginInstallError(f"{what} timed out after {timeout_s} s") from None
    except OSError as exc:
        raise PluginInstallError(f"{what}: {exc}") from None


def _failure(what: str, cp: subprocess.CompletedProcess, secrets: list[str]) -> PluginInstallError:
    detail = first_line(cp.stderr, secrets) or first_line(cp.stdout, secrets)
    return PluginInstallError(f"{what} failed (exit {cp.returncode}): {detail or 'no output'}")


def _known_marketplaces(config_dir: Path) -> set[str]:
    """Marketplace names in plugins/known_marketplaces.json; empty if unreadable."""
    try:
        known = json.loads((config_dir / "plugins" / "known_marketplaces.json").read_text())
    except (OSError, ValueError):
        return set()
    return set(known) if isinstance(known, dict) else set()


def _ensure_marketplaces(
    config_dir: Path, plugins: list[str], env: dict[str, str], timeout_s: int, secrets: list[str]
) -> None:
    """Configure (or refresh) every marketplace the plugin keys name.

    All keys are checked against MARKETPLACES before anything runs. A known
    marketplace, or one `add` reports as already there, is updated instead.
    """
    names: list[str] = []
    for key in plugins:
        name = key.rpartition("@")[2] if "@" in key else ""
        if name not in MARKETPLACES:
            raise PluginInstallError(
                f"plugin {key}: marketplace {name or '(none)'} has no source in"
                " sandbox.MARKETPLACES; add it there (name -> owner/repo)"
            )
        if name not in names:
            names.append(name)
    for name in names:
        what = f"adding marketplace {name}"
        if name not in _known_marketplaces(config_dir):
            cp = _plugin_cli(
                ["marketplace", "add", MARKETPLACES[name], "--scope", "user", "--json"],
                config_dir, env, timeout_s, what,
            )  # fmt: skip
            if cp.returncode == 0:
                continue
            if "already" not in (cp.stdout + cp.stderr).lower():
                raise _failure(what, cp, secrets)
        what = f"updating marketplace {name}"
        cp = _plugin_cli(["marketplace", "update", name, "--json"], config_dir, env, timeout_s, what)
        if cp.returncode != 0:
            raise _failure(what, cp, secrets)


def install_plugins(
    config_dir: Path, plugins: list[str], env: dict[str, str], *, timeout_s: int = 300
) -> dict[str, str]:
    """Install the profile's plugins into the fresh config dir; return plugin_versions.

    Listing them in `enabledPlugins` alone is not enough: Claude Code then
    fetches the marketplace but installs nothing (observed live). And
    `plugin install` does not fetch a marketplace itself, so every
    marketplace the keys name is added (or updated) first. `env` is the
    run's child_env, so CLAUDE_CONFIG_DIR points at `config_dir`.

    Success means exit code 0 and the key in installed_plugins.json; the
    `--json` result line has no documented shape. A plugin that wants a
    marketplace-declared command run is never accepted automatically (no
    `-y`, no `--accept-command`): that needs a person. Raises
    PluginInstallError naming the plugin.
    """
    if not plugins:
        return {}
    secrets = [env[k] for k in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN") if env.get(k)]
    settings_path = config_dir / "settings.json"
    before = _read_settings(settings_path)
    _ensure_marketplaces(config_dir, plugins, env, timeout_s, secrets)
    for key in plugins:
        what = f"installing plugin {key}"
        cp = _plugin_cli(["install", key, "--scope", "user", "--json"], config_dir, env, timeout_s, what)
        output = cp.stdout + "\n" + cp.stderr
        if "shownCommand" in output or "--accept-command" in output:
            raise PluginInstallError(
                f"plugin {key} wants to run a marketplace-declared command, which the lab never"
                " accepts on its own: review that command and install the plugin by hand"
                f" (`claude plugin install {key}`), or remove it from the profile"
            )
        if cp.returncode != 0:
            raise _failure(what, cp, secrets)
    try:
        versions = plugin_versions(config_dir)
    except (OSError, ValueError):
        versions = {}
    missing = [key for key in plugins if key not in versions]
    if missing:
        raise PluginInstallError(
            f"plugin(s) {', '.join(missing)} missing from plugins/installed_plugins.json after install"
        )
    restored = _restore_settings(settings_path, before)
    if restored:
        print(f"note: restored {', '.join(restored)} in settings.json after plugin install", file=sys.stderr)
    return versions


def reset_repo(gh: GitHub, test_id: str, repo_dir: Path, *, lab_root: Path) -> None:
    """Give the test a fresh, empty GitHub repo and an initialised local checkout.

    Wipes only the test's own checkout, `<lab_root>/sandbox/<test_id>/xyz`; any
    other directory is refused before anything is deleted or called. If
    GitHub refuses the delete or create, the reset starts over after each of
    RESET_PAUSES_S; the last refusal raises GitHubError.
    """
    repo_dir = repo_dir.resolve()
    if repo_dir != paths(lab_root, test_id).repo:
        raise ValueError(f"refusing to reset {repo_dir}: not the checkout of test {test_id!r}")
    name = f"lab-{test_id}"
    for pause in (*RESET_PAUSES_S, None):
        try:
            if gh.repo_exists(name):
                gh.delete_repo(name)
            url = gh.create_repo(name)
            break
        except GitHubError as exc:
            if pause is None:
                raise
            print(f"note: GitHub refused to reset {name} ({first_line(str(exc), [gh.token])});"
                  f" trying again in {pause} s", file=sys.stderr)
            time.sleep(pause)
    shutil.rmtree(repo_dir, ignore_errors=True)
    repo_dir.mkdir(parents=True)
    configure_repo(repo_dir, url, gh.git_env(), gh.token)
