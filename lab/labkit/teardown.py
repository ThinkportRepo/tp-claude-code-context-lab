"""`lab.py teardown`: delete the lab's repos and sandbox, optionally revoke its tokens.

Nothing is deleted before `confirm` says yes. Tokens are only ever shown
masked (or not at all) and travel in a request body, a request header or
a child's environment, never in argv.

A token counts as revoked only when a check with the old token proves it
is dead. Anything else is reported with the masked token and the page
where the user can revoke or check it, and teardown exits 1.
"""

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from . import env, limits, preflight
from .github import S5_REPO, GitHub, without_credentials
from .github_auth import open_url
from .runner import headless_argv

REPO_PREFIX = "lab-test"
CLAUDE_TOKENS_URL = "https://claude.ai/settings/claude-code"
GITHUB_REVOKE_URL = "https://api.github.com/credentials/revoke"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_TOKENS_URL = "https://github.com/settings/personal-access-tokens"
CLAUDE_CHECK_TIMEOUT_S = 120
_HTTP_TIMEOUT_S = 30
# The revoke endpoint answers 202 Accepted, so the token may still work for
# a moment: a check that finds it alive is repeated a few times.
_GITHUB_CHECK_TRIES = 4
_GITHUB_CHECK_PAUSE_S = 5

# What a check with an old token can tell.
REVOKED = "revoked"
STILL_ACTIVE = "still active"
UNVERIFIED = "not verified"
# Unambiguous signs that the Claude credential itself was rejected, in the
# check's result event or stderr. Anything else (a bad request or model, no
# network, rate or usage limit, overload) says nothing about the token, so
# a bare "invalid" does not count, and neither does a bare number 401 (a
# stack-trace line such as `cli.js:401:12`).
_AUTH_FAILURE = re.compile(
    r"(?:API Error|HTTP|status)\W*401\b|authentication_error|invalid bearer token|invalid x-api-key|invalid api key"
    r"|token has been revoked|oauth token has expired|oauth token is invalid",
    re.IGNORECASE,
)


def _lab_repos(gh: GitHub) -> list[str]:
    return [n for n in gh.list_repos("lab-") if n.startswith(REPO_PREFIX) or n == S5_REPO]


def _sandbox_dirs(sandbox: Path) -> list[Path]:
    if not sandbox.is_dir():
        return []
    return sorted(p for p in sandbox.iterdir() if p.is_dir() and not p.is_symlink())


def plan(gh: GitHub, sandbox: Path) -> tuple[list[str], list[Path]]:
    """What teardown would delete: (lab repos in the org, directories under sandbox/).

    Repos are `lab-test*` and the S5 check's repo; nothing else in the org.
    Symlinks are not followed or listed, so nothing outside sandbox/ is touched.
    """
    return _lab_repos(gh), _sandbox_dirs(sandbox)


def run(
    lab_root: Path,
    cfg: dict[str, str],
    gh: GitHub,
    *,
    secrets: bool,
    confirm: Callable[[str], bool],
    now: datetime | None = None,
) -> int:
    """Print the plan, ask once, then delete; with `secrets` also revoke the tokens.

    Without usable GitHub settings (missing, expired, or the listing fails)
    the repos and the GitHub token are skipped, with a hint; the sandbox
    and the Claude token are still handled. Returns 0 if everything asked
    for was done and every token is proven revoked, else 1 (declined,
    skipped, a step failed, or a token is still active or not verified).
    """
    problem = preflight.github_unusable(cfg, now or datetime.now(timezone.utc))
    repos: list[str] = []
    if problem is None:
        try:
            repos = _lab_repos(gh)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            problem = str(exc)  # GitHubError messages never contain the token
    if problem:
        print(
            f"GitHub: {problem} — lab repos cannot be listed or deleted. To delete them, "
            "run `python3 lab.py tokens github`, then teardown again."
        )
    dirs = _sandbox_dirs(lab_root / "sandbox")
    _print_plan(cfg.get("LAB_GH_ORG", "?"), repos, dirs, secrets, github_ok=problem is None)
    if not repos and not dirs and not secrets:
        print("Nothing to delete.")
        return 0 if problem is None else 1
    if not confirm(_question(repos, dirs, secrets)):
        print("Nothing was deleted.")
        return 1

    ok = problem is None
    repos_failed = False
    for name in repos:
        try:
            gh.delete_repo(name)
            print(f"deleted repo {name}")
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            repos_failed, ok = True, False
    for path in dirs:
        try:
            shutil.rmtree(path)
            print(f"deleted {path}")
        except OSError as exc:
            print(f"error: cannot delete {path}: {exc}", file=sys.stderr)
            ok = False

    if secrets:
        env_file = lab_root / ".env"
        if problem:
            if cfg.get("GH_TOKEN"):
                print("GitHub token kept: GitHub was skipped (see above).")
        elif repos_failed:
            # The token is needed to retry the deletion, so it stays.
            print("GitHub token kept: some repos could not be deleted. Run teardown again.")
            ok = False
        elif cfg.get("GH_TOKEN"):
            ok = _revoke_github(env_file, cfg["GH_TOKEN"]) and ok
        if cfg.get("CLAUDE_CODE_OAUTH_TOKEN"):
            ok = _revoke_claude(env_file, cfg["CLAUDE_CODE_OAUTH_TOKEN"]) and ok
        else:
            print("No Claude token stored.")
        if env_file.exists() and not env.load(env_file, {}):
            env_file.unlink()
            print(f"deleted {env_file} (no settings left)")
    return 0 if ok else 1


def _print_plan(org: str, repos: list[str], dirs: list[Path], secrets: bool, *, github_ok: bool) -> None:
    if github_ok:
        print(f"Repos in {org} to delete ({len(repos)}):")
        print("\n".join(f"  {name}" for name in repos) or "  none")
    else:
        print(f"Repos in {org}: skipped (see above)")
    print(f"Sandbox directories to delete, incl. run transcripts under results/ ({len(dirs)}):")
    print("\n".join(f"  {path}" for path in dirs) or "  none")
    if secrets:
        print(f"Tokens to revoke: {'GitHub token, ' if github_ok else ''}Claude token")


def _question(repos: list[str], dirs: list[Path], secrets: bool) -> str:
    what = f"{len(repos)} repos and {len(dirs)} sandbox directories (incl. run transcripts under results/)"
    return f"Delete {what}{' and revoke the tokens' if secrets else ''}? [y/N]"


def _revoke_github(env_file: Path, token: str) -> bool:
    """Ask GitHub to revoke the PAT, check it is dead, drop it from .env.

    The revoke endpoint needs no other credential and its 202 Accepted
    proves nothing, so a GET /user with the old token decides. The token
    leaves .env in every case (the lab no longer needs it); True only if
    the check proves it revoked, otherwise the user is told where to look.
    """
    masked = env.mask(token)
    request = urllib.request.Request(
        GITHUB_REVOKE_URL,
        data=json.dumps({"credentials": [token]}).encode(),
        method="POST",
        headers={**_GITHUB_HEADERS, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S):
            pass
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:  # HTTPError is a URLError
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        reason = str(exc).replace(token, "***")
        print(
            f"warning: the revoke request for the GitHub token {masked} failed ({reason});"
            " checking whether the token still works",
            file=sys.stderr,
        )
    state = _github_token_state(token)
    env.remove(env_file, "GH_TOKEN")
    env.remove(env_file, "GH_TOKEN_EXPIRES_AT")
    if state == REVOKED:
        print(f"GitHub token {masked}: revoked and removed from .env")
    elif state == STILL_ACTIVE:
        print(
            f"The GitHub token {masked} is still active. It was removed from .env;"
            f" revoke it at {GITHUB_TOKENS_URL}."
        )
    else:
        print(
            f"Could not verify that the GitHub token {masked} was revoked. It was removed"
            f" from .env; open {GITHUB_TOKENS_URL} and make sure the lab's token is revoked."
        )
    return state == REVOKED


_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "claude-code-context-lab",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _github_token_state(token: str) -> str:
    """REVOKED, STILL_ACTIVE or UNVERIFIED; a live token is asked again a few times."""
    for attempt in range(_GITHUB_CHECK_TRIES):
        if attempt:
            time.sleep(_GITHUB_CHECK_PAUSE_S)
        state = _github_token_check(token)
        if state != STILL_ACTIVE:
            return state
    return STILL_ACTIVE


def _github_token_check(token: str) -> str:
    """One GET /user with the old token: 401 is REVOKED, 200 STILL_ACTIVE, else UNVERIFIED.

    The token travels only in the Authorization header, which urllib keeps
    off any redirect to another host.
    """
    request = urllib.request.Request(GITHUB_USER_URL, headers=_GITHUB_HEADERS)
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as resp:
            return STILL_ACTIVE if resp.status == 200 else UNVERIFIED
    except urllib.error.HTTPError as exc:
        exc.close()
        return REVOKED if exc.code == 401 else UNVERIFIED
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        return UNVERIFIED


def _revoke_claude(env_file: Path, token: str) -> bool:
    """Have the user revoke the token on claude.ai, test it, drop it from .env.

    True only if the test proves the token revoked; otherwise the user is
    told, with the masked token, what to check on claude.ai.
    """
    open_url(CLAUDE_TOKENS_URL)
    print("Revoke every 'Claude Code' entry the lab created, then press Enter")
    if sys.stdin.isatty():
        try:
            input()
        except EOFError:
            pass
    state = _claude_token_state(token)
    masked = env.mask(token)
    if state == REVOKED:
        print(f"Claude token {masked}: revoked")
    elif state == STILL_ACTIVE:
        print(f"The Claude token {masked} is still active. Revoke it at {CLAUDE_TOKENS_URL}.")
    else:
        print(
            f"Could not verify that the Claude token {masked} was revoked. Open {CLAUDE_TOKENS_URL}"
            ' and make sure every "Claude Code" entry the lab created is revoked.'
        )
    env.remove(env_file, "CLAUDE_CODE_OAUTH_TOKEN")
    print("Claude token removed from .env")
    return state == REVOKED


def _claude_token_state(token: str) -> str:
    """Try one minimal call with the old token: REVOKED, STILL_ACTIVE or UNVERIFIED.

    Runs in a fresh, existing config dir, with no outside Claude, Anthropic
    or GitHub credential in the environment. Only an unambiguous
    authentication failure (_AUTH_FAILURE) counts as revoked; any other
    failure leaves the state unknown.
    """
    with tempfile.TemporaryDirectory(prefix="lab-teardown-") as tmp:
        config_dir = Path(tmp) / "config"
        config_dir.mkdir()
        child = without_credentials(os.environ)
        child.update(CLAUDE_CONFIG_DIR=str(config_dir), CLAUDE_CODE_OAUTH_TOKEN=token)
        try:
            cp = subprocess.run(
                headless_argv(limits.PROBE_PROMPT, limits.PROBE_MODEL),
                cwd=tmp,
                env=child,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=CLAUDE_CHECK_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError):
            return UNVERIFIED
    if cp.returncode == 0:
        return STILL_ACTIVE
    return REVOKED if _AUTH_FAILURE.search(_failure_text(cp)) else UNVERIFIED


def _failure_text(cp: subprocess.CompletedProcess) -> str:
    """stderr plus the error text of every `result` event on stdout."""
    parts = [cp.stderr or ""]
    for line in (cp.stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            parts += [str(event[key]) for key in ("result", "error", "errors") if event.get(key)]
    return "\n".join(parts)
