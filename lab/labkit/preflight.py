"""Checks that run once before a lab command touches anything.

`check` is pure apart from looking up tools on PATH and reading the sandbox's
parent directories: no network calls. Messages name variables and give a
hint, but never contain a secret value.
"""

import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_HINT = "run `python3 lab.py tokens claude`"
GITHUB_HINT = "run `python3 lab.py tokens github`"
# Memory files Claude Code loads from parent directories. It reads
# `.claude/settings*.json` only in the project directory itself, so a
# parent's settings are not listed here.
PARENT_MEMORY_FILES = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")
# Where Google Chrome lives on macOS (the per-user directory is `home / "Applications"`).
MACOS_APP_DIRS = (Path("/Applications"),)
# Executable names of Google Chrome on PATH on other systems.
CHROME_COMMANDS = ("google-chrome", "google-chrome-stable", "chrome")


def check(
    cfg: dict[str, str],
    *,
    need_github: bool,
    need_node: bool,
    need_chrome: bool,
    need_claude_token: bool = True,
    timeout_s: int,
    now: datetime,
    sandbox: Path,
    home: Path,
) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for the current setup.

    `need_node` and `need_chrome` are for profiles with MCP servers: their
    servers start through `npx`, and the playwright server drives Google
    Chrome. Without `npx` the servers do not start; without Chrome the
    browser tools fail. Either way a run would not fail but measure a
    different session, so both are errors.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if sys.version_info < (3, 11):
        errors.append("Python 3.11 or newer is required")

    tools = ["claude", "git", "gh"] + (["npx"] if need_node else [])
    for tool in tools:
        if not shutil.which(tool):
            errors.append(f"`{tool}` not found on PATH")

    if need_chrome and not chrome_installed(home):
        errors.append(
            "Google Chrome is not installed, and lab/matrix.conf uses an MCP profile whose"
            " playwright MCP server drives it (verified 2026-10-14); install Google Chrome"
            " or remove the tests of that profile"
        )

    if need_claude_token and not cfg.get("CLAUDE_CODE_OAUTH_TOKEN"):
        errors.append(f"CLAUDE_CODE_OAUTH_TOKEN is not set: {CLAUDE_HINT}")

    if need_github:
        if not cfg.get("LAB_GH_ORG"):
            errors.append("LAB_GH_ORG is not set: set it in .env or the environment")
        if not cfg.get("GH_TOKEN"):
            errors.append(f"GH_TOKEN is not set: {GITHUB_HINT}")
        else:
            _check_github_expiry(cfg, timeout_s, now, errors, warnings)

    warnings.extend(_parent_config_warnings(sandbox, home))
    return errors, warnings


def chrome_installed(home: Path, which=shutil.which) -> bool:
    """True if Google Chrome is installed: an app bundle on macOS, a command on PATH elsewhere."""
    if sys.platform == "darwin":
        app_dirs = (*MACOS_APP_DIRS, home / "Applications")
        return any((d / "Google Chrome.app").exists() for d in app_dirs)
    return any(which(name) for name in CHROME_COMMANDS)


def _check_github_expiry(
    cfg: dict[str, str], timeout_s: int, now: datetime,
    errors: list[str], warnings: list[str],
) -> None:
    raw = cfg.get("GH_TOKEN_EXPIRES_AT")
    if not raw:
        warnings.append("GH_TOKEN_EXPIRES_AT is not set: cannot tell whether GH_TOKEN is still valid")
        return
    try:
        expires = _parse_expiry(raw)
    except ValueError:
        errors.append(f"GH_TOKEN_EXPIRES_AT is not an ISO 8601 timestamp: {GITHUB_HINT}")
        return
    if expires < now + timedelta(seconds=timeout_s):
        errors.append(
            f"GH_TOKEN expires at {expires.isoformat()}, "
            f"less than {timeout_s} s from now: {GITHUB_HINT}"
        )


def _parse_expiry(raw: str) -> datetime:
    """GH_TOKEN_EXPIRES_AT as an aware datetime (UTC if it has no zone); ValueError if unreadable."""
    expires = datetime.fromisoformat(raw)
    return expires.replace(tzinfo=timezone.utc) if expires.tzinfo is None else expires


def github_unusable(cfg: dict[str, str], now: datetime) -> str | None:
    """Why the lab's GitHub settings cannot work right now, or None if they might.

    For teardown, which has to work with whatever is left: no time margin,
    and a missing or unreadable expiry is no reason to skip (a failing call
    reports it instead).
    """
    for key in ("LAB_GH_ORG", "GH_TOKEN"):
        if not cfg.get(key):
            return f"{key} is not set"
    try:
        expires = _parse_expiry(cfg.get("GH_TOKEN_EXPIRES_AT") or "")
    except ValueError:
        return None
    return f"GH_TOKEN expired at {expires.isoformat()}" if expires <= now else None


def _parent_config_warnings(sandbox: Path, home: Path) -> list[str]:
    """Warn about memory files in the sandbox's parents that Claude Code would load."""
    own_memory = home / ".claude" / "CLAUDE.md"  # the user's own memory, not project memory
    found = []
    for parent in sandbox.parents:
        for name in PARENT_MEMORY_FILES:
            candidate = parent / name
            if candidate != own_memory and candidate.exists():
                found.append(
                    f"{candidate} is in a parent directory of the sandbox "
                    "and may be loaded by Claude Code as memory, which skews token counts"
                )
    return found
