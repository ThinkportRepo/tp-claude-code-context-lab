"""GitHub operations for the lab, run through the `gh` CLI.

Every call uses the lab's own token and an empty `gh` config dir, so the
user's personal `gh` login and git configuration are never touched or used.
The token only ever travels in the child's environment, never in argv.
"""

import http.client
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

S5_REPO = "lab-s5check"
S5_TEXT = "Hello GitHub Pages"
_PAGES_POLL_S = 10
_TIMEOUT_S = 120

# GitHub credentials a child could pick up from the parent environment;
# none may leak into (or override) the lab token.
GITHUB_TOKENS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
# A running SSH agent would let a child authenticate as the user over SSH.
SSH_AGENT = ("SSH_AUTH_SOCK", "SSH_AGENT_PID")


class GitHubError(RuntimeError):
    """A gh or git call failed; the message never contains the token."""


def without_credentials(environ: Mapping[str, str]) -> dict[str, str]:
    """`environ` without GitHub tokens, the SSH agent and CLAUDE*/ANTHROPIC* variables.

    The base of every child environment: the caller adds back exactly the
    credentials that child needs.
    """
    return {
        k: v for k, v in environ.items()
        if k not in GITHUB_TOKENS + SSH_AGENT and not k.startswith(("CLAUDE", "ANTHROPIC"))
    }  # fmt: skip


def gh_env(environ: Mapping[str, str], token: str, gh_config_dir: Path) -> dict[str, str]:
    """Environment for `gh` and `git` child processes.

    Pins the lab token, an empty gh config dir and no prompts, and cuts git
    off from the user's global/system config so credential helpers such as
    osxkeychain never apply. gh and git never need Claude's variables, so
    those are dropped too.
    """
    child = without_credentials(environ)
    child.update(
        GH_TOKEN=token,
        GH_CONFIG_DIR=str(gh_config_dir),
        GH_PROMPT_DISABLED="1",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
    )
    return child


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


class GitHub:
    """The few GitHub operations the lab needs, scoped to one organization."""

    def __init__(self, org: str, token: str, gh_config_dir: Path):
        self.org = org
        self.token = token
        self.gh_config_dir = gh_config_dir

    def git_env(self) -> dict[str, str]:
        """gh_env for this token and gh config dir, for `gh` and `git` children."""
        return gh_env(os.environ, self.token, self.gh_config_dir)

    def _gh(self, *args: str) -> subprocess.CompletedProcess:
        self.gh_config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return subprocess.run(
            ["gh", *args],
            env=self.git_env(),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )

    def _fail(self, what: str, cp: subprocess.CompletedProcess) -> GitHubError:
        detail = (cp.stderr.strip() or cp.stdout.strip() or f"exit code {cp.returncode}")
        return GitHubError(f"{what}: {_scrub(detail, self.token)}")

    @staticmethod
    def _not_found(cp: subprocess.CompletedProcess) -> bool:
        return "404" in cp.stderr or "Not Found" in cp.stderr

    def org_ok(self) -> bool:
        return self._gh("api", f"orgs/{self.org}", "--silent").returncode == 0

    def repo_exists(self, name: str) -> bool:
        cp = self._gh("api", f"repos/{self.org}/{name}", "--silent")
        if cp.returncode == 0:
            return True
        if self._not_found(cp):
            return False
        raise self._fail(f"check repo {name}", cp)

    def create_repo(self, name: str) -> str:
        """Create a public repo in the org and return its HTTPS clone URL."""
        cp = self._gh(
            "api", "--method", "POST", f"orgs/{self.org}/repos",
            "-f", f"name={name}", "-F", "private=false",
        )
        if cp.returncode != 0:
            raise self._fail(f"create repo {name}", cp)
        return json.loads(cp.stdout)["clone_url"]

    def delete_repo(self, name: str) -> None:
        cp = self._gh("api", "--method", "DELETE", f"repos/{self.org}/{name}")
        if cp.returncode != 0 and not self._not_found(cp):
            raise self._fail(f"delete repo {name}", cp)

    def list_repos(self, prefix: str) -> list[str]:
        cp = self._gh("api", "--paginate", f"orgs/{self.org}/repos", "--jq", ".[].name")
        if cp.returncode != 0:
            raise self._fail("list repos", cp)
        return [n for n in cp.stdout.split() if n.startswith(prefix)]

    def enable_pages(self, name: str, branch: str = "main", path: str = "/") -> None:
        cp = self._gh(
            "api", "--method", "POST", f"repos/{self.org}/{name}/pages",
            "-f", f"source[branch]={branch}", "-f", f"source[path]={path}",
        )
        if cp.returncode != 0:
            raise self._fail(f"enable pages for {name}", cp)

    def pages_status(self, name: str) -> dict | None:
        """The Pages site as GitHub reports it, or None if Pages is not enabled."""
        cp = self._gh("api", f"repos/{self.org}/{name}/pages")
        if cp.returncode == 0:
            return json.loads(cp.stdout)
        if self._not_found(cp):
            return None
        raise self._fail(f"read pages status of {name}", cp)

    def homepage(self, name: str) -> str | None:
        """The repo's About -> Website field (`homepage`); None if empty or the repo is gone."""
        cp = self._gh("api", f"repos/{self.org}/{name}")
        if cp.returncode == 0:
            return (json.loads(cp.stdout).get("homepage") or "").strip() or None
        if self._not_found(cp):
            return None
        raise self._fail(f"read repo {name}", cp)

    def pages_ok(self, name: str, text: str, wait_s: int) -> bool:
        """Poll up to wait_s (every 10 s) until the Pages site is live and shows text.

        Used by check_s5, which never sets a website; runs check goal_ok.
        """
        return self._poll_pages(
            name, wait_s, lambda url: True if _page_shows(url, text) else None
        )

    def goal_ok(self, name: str, wait_s: int) -> bool:
        """Is the lab's goal met? Poll up to wait_s (every 10 s) only until the site is live.

        The goal: the Pages site is published (answers 200), and the repo's
        About -> Website field links to it. What the page shows is not part
        of the goal. Waiting is for the deployment only (no Pages yet, still
        building, not answering yet). Once the page answers the answer is
        final: the link comes from an agent action and does not appear on its
        own, so a missing or different homepage is False right away.
        """
        def decide(url: str) -> bool | None:
            if not _page_live(url):
                return None  # not live yet: keep polling
            return _same_site(self.homepage(name), url)

        return self._poll_pages(name, wait_s, decide)

    def _poll_pages(
        self, name: str, wait_s: int, decide: Callable[[str], bool | None]
    ) -> bool:
        """Poll up to wait_s (every 10 s) until the site is deployed and decide(html_url) is final.

        `decide` returns True or False when the answer is known and None while
        the site is not live yet; None means poll again, until wait_s is used up.

        Branch ("legacy") builds report a build status, so `decide` only runs
        once that is "built". Builds deployed by a GitHub Actions workflow
        report `status: null` while the site is live, so for those `decide`
        alone rules. A failed check (e.g. CDN lag right after a deploy) is
        retried on the next poll.
        """
        waited = 0
        while True:
            status = self.pages_status(name)
            if status and status.get("html_url") and (
                status.get("build_type") == "workflow"
                or status.get("status") in (None, "built")
            ):
                verdict = decide(status["html_url"])
                if verdict is not None:
                    return verdict
            if waited >= wait_s:
                return False
            time.sleep(_PAGES_POLL_S)
            waited += _PAGES_POLL_S


def _site_key(url: str) -> str:
    """url without surrounding whitespace, http(s) scheme and one trailing slash; host lowercased.

    Host names are case-insensitive, paths are not, so only the host is folded.
    """
    url = url.strip()
    for scheme in ("https://", "http://"):
        if url[: len(scheme)].lower() == scheme:
            url = url[len(scheme):]
            break
    else:
        return ""  # no http(s) URL: never the same site
    host, slash, path = url.partition("/")
    return (host.lower() + slash + path).removesuffix("/")


def _same_site(homepage: str | None, html_url: str) -> bool:
    """True if the About -> Website value points at the Pages URL.

    Tolerates one trailing slash, http vs https and host case; anything else must match.
    """
    key = _site_key(homepage or "")
    return bool(key) and key == _site_key(html_url)


def _page_live(url: str) -> bool:
    """True if GET url answers 200, whatever the body; see _page_shows."""
    return _page_shows(url, "")


def _page_shows(url: str, text: str) -> bool:
    """True if GET url answers 200 and the body contains text; never raises on HTTP errors.

    A broken response (bad status line, body cut short) counts as not shown.
    Each check adds its own query string, so a cached page of an earlier
    (deleted and recreated) repository cannot pass.
    """
    fetch_url = f"{url}{'&' if '?' in url else '?'}lab-check={time.monotonic_ns()}"
    try:
        with urllib.request.urlopen(fetch_url, timeout=30) as resp:
            return resp.status == 200 and text in resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        e.close()
        return False
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        return False


def _git(repo_dir: Path, env: Mapping[str, str], token: str, *args: str) -> None:
    cp = subprocess.run(
        ["git", *args], cwd=repo_dir, env=dict(env),
        capture_output=True, text=True, timeout=_TIMEOUT_S,
    )
    if cp.returncode != 0:
        detail = (cp.stderr.strip() or cp.stdout.strip() or f"exit code {cp.returncode}")
        command = args[2] if args[:1] == ("-c",) else args[0]  # skip a leading `-c key=value`
        raise GitHubError(f"git {command}: {_scrub(detail, token)}")


def configure_repo(repo_dir: Path, remote_url: str, env: Mapping[str, str], token: str = "") -> None:
    """Initialise repo_dir so pushes authenticate with the lab token via gh.

    `env` is gh_env's environment, so the user's global git config never
    applies; the empty `init.templateDir` also keeps hook templates out of
    `.git/hooks`. The empty `credential.helper` first resets any inherited
    helper list; the second one makes git ask `gh`, which reads GH_TOKEN
    from the environment. Raises GitHubError with git's stderr (token
    scrubbed) if a step fails.
    """
    def git(*args: str) -> None:
        _git(repo_dir, env, token, *args)

    git("-c", "init.templateDir=", "init", "-b", "main")
    git("remote", "add", "origin", remote_url)
    git("config", "--local", "credential.helper", "")
    git("config", "--local", "--add", "credential.helper", "!gh auth git-credential")
    git("config", "--local", "user.name", "Claude Code Context Lab")
    git("config", "--local", "user.email", "lab@users.noreply.github.com")


def _remove_local_copy(repo_dir: Path, sandbox: Path) -> None:
    """Delete repo_dir, but only if it resolves to a direct child of the sandbox.

    Same rule as sandbox.paths: a symlink or path leading out of the sandbox
    is left alone. Never raises; a leftover copy is harmless and gets wiped
    by the next check.
    """
    resolved = repo_dir.resolve()
    if resolved.name == S5_REPO and resolved.parent == sandbox.resolve():
        shutil.rmtree(resolved, ignore_errors=True)


def check_s5(cfg: dict[str, str], sandbox: Path) -> list[tuple[str, bool, str]]:
    """Run the whole repo lifecycle once and report each step as (step, ok, detail).

    Stops at the first failing step, but always deletes the test repo again
    if it was created, and its local working copy under the sandbox in any
    case. `detail` never contains the token.
    """
    org, token = cfg["LAB_GH_ORG"], cfg["GH_TOKEN"]
    gh_dir = sandbox / ".gh"
    gh = GitHub(org, token, gh_dir)
    repo_dir = sandbox / S5_REPO
    env = gh_env(os.environ, token, gh_dir)
    rows: list[tuple[str, bool, str]] = []
    created = False

    def step(name: str, action) -> bool:
        try:
            ok, detail = True, action() or ""
        except Exception as e:  # report any failure as a row, never crash the check
            ok, detail = False, _scrub(str(e), token)
        rows.append((name, ok, detail))
        return ok

    def org_access() -> str:
        if not gh.org_ok():
            raise GitHubError(f"cannot read organization {org}")
        return org

    def create() -> str:
        nonlocal created
        if gh.repo_exists(S5_REPO):  # left over from an interrupted run
            gh.delete_repo(S5_REPO)
        url = gh.create_repo(S5_REPO)
        created = True
        return url

    def commit() -> str:
        shutil.rmtree(repo_dir, ignore_errors=True)
        repo_dir.mkdir(parents=True)
        configure_repo(repo_dir, f"https://github.com/{org}/{S5_REPO}.git", env, token)
        (repo_dir / "index.html").write_text(f"<!doctype html><h1>{S5_TEXT}</h1>\n")
        _git(repo_dir, env, token, "add", "index.html")
        _git(repo_dir, env, token, "commit", "-m", "Add index.html")
        return "index.html"

    def push() -> str:
        _git(repo_dir, env, token, "push", "-u", "origin", "main")
        return "main"

    def pages_served() -> str:
        if not gh.pages_ok(S5_REPO, S5_TEXT, wait_s=180):
            raise GitHubError("Pages not built or text missing within 180 s")
        return ""

    steps = [
        ("org access", org_access),
        ("create repo", create),
        ("commit", commit),
        ("push over HTTPS", push),
        ("enable pages", lambda: gh.enable_pages(S5_REPO)),
        ("pages serve content", pages_served),
    ]
    try:
        for name, action in steps:
            if not step(name, action):
                break
    finally:
        if created:
            step("delete repo", lambda: gh.delete_repo(S5_REPO))
        _remove_local_copy(repo_dir, sandbox)
    return rows
