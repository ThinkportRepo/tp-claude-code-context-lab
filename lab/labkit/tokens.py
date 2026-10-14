"""Creating the lab's credentials and storing them in .env."""

import os
import pty
import re
import select
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import env, github, github_auth

# CSI sequences (colors, cursor moves) and OSC sequences (titles, links).
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CLAUDE_TOKEN = re.compile(r"sk-ant-oat\d+-[A-Za-z0-9_-]+")
_CLAUDE_TOKEN_BYTES = re.compile(_CLAUDE_TOKEN.pattern.encode())
# The end of a read that may be the start of a token continuing in the next
# read: part of the `sk-ant-oat` prefix, or a token running up to the end.
_TOKEN_TAIL = re.compile(
    rb"(?:sk-ant-oat\d*(?:-[A-Za-z0-9_-]*)?|"
    + b"|".join(b"sk-ant-oat"[:n] for n in range(9, 0, -1))
    + rb")\Z"
)
# How long a read that is all held back waits for the rest of a token.
_HOLD_S = 0.5
CLEAR_SCREEN = "\x1b[H\x1b[2J\x1b[3J"  # home, clear screen, clear scrollback
SCREEN_WARNING = (
    "Claude Code's login screen shows the new token; the lab masks it where it can."
    " Until this is done, don't share or record your screen."
)

# Wide enough that the terminal UI never wraps the token across lines,
# which is what breaks copying it by hand.
_PTY_COLUMNS = 500


def extract_claude_token(output: str) -> str | None:
    """Return the last setup token printed in the captured terminal output."""
    found = _CLAUDE_TOKEN.findall(_ANSI.sub("", output))
    return found[-1] if found else None


class _ScreenMask:
    """Masks setup tokens in the bytes passed on to the terminal.

    The end of a read that may be the start of a token is held back until
    the next read shows how it continues, so a token split across reads is
    masked too (best effort: it assumes the token is printed in one piece).
    """

    def __init__(self) -> None:
        self._held = b""

    def feed(self, data: bytes) -> bytes:
        data = self._held + data
        tail = _TOKEN_TAIL.search(data)
        cut = tail.start() if tail else len(data)
        self._held = data[cut:]
        return _mask(data[:cut])

    def flush(self) -> bytes:
        data, self._held = self._held, b""
        return _mask(data)


def _mask(data: bytes) -> bytes:
    return _CLAUDE_TOKEN_BYTES.sub(lambda m: env.mask(m.group().decode()).encode(), data)


def _read_more(fd: int) -> bytes:
    """Up to 4096 more bytes if the child writes within _HOLD_S, else b''."""
    try:
        ready, _, _ = select.select([fd], [], [], _HOLD_S)
        return os.read(fd, 4096) if ready else b""
    except OSError:
        return b""


def create_claude_token(env_file: Path, config_dir: Path | None = None) -> int:
    """Run `claude setup-token` interactively and store the token in env_file.

    The command runs in a pseudo-terminal so its browser prompt works as
    usual, while the output is also captured to read the token from it.
    What reaches the screen has the token masked, and the screen and its
    scrollback are cleared once the token is stored. Without config_dir it
    runs in a throwaway config dir, so no existing Claude login is touched.
    """
    claude = shutil.which("claude")
    if not claude:
        print("claude CLI not found on PATH", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="lab-claude-") as tmp:
        child_env = dict(os.environ, CLAUDE_CONFIG_DIR=str(config_dir or tmp))
        # An existing token in the environment would only confuse setup-token.
        child_env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        # The token belongs to whichever account is signed in in the browser.
        # LAB_BROWSER picks a browser (e.g. a private window) for the login.
        if os.environ.get("LAB_BROWSER"):
            child_env["BROWSER"] = os.environ["LAB_BROWSER"]

        print(f"warning: {SCREEN_WARNING}")
        print("Sign in with the account whose subscription the lab should measure.\n")

        captured = bytearray()
        screen = _ScreenMask()

        def read(fd: int) -> bytes:
            data = os.read(fd, 4096)
            captured.extend(data)
            shown = screen.feed(data) if data else screen.flush()
            # pty.spawn takes an empty result for the end of the output, so a
            # read that is all held back waits briefly for the rest instead.
            while data and not shown:
                data = _read_more(fd)
                captured.extend(data)
                shown = screen.feed(data) if data else screen.flush()
            return shown

        # pty.spawn takes no environment, so set it for the child only.
        saved = os.environ.copy()
        os.environ.clear()
        os.environ.update(child_env)
        try:
            status = pty.spawn(
                ["/bin/sh", "-c", f'stty cols {_PTY_COLUMNS} 2>/dev/null; exec "$0" setup-token', claude],
                read,
            )
        finally:
            os.environ.clear()
            os.environ.update(saved)

    token = extract_claude_token(captured.decode("utf-8", errors="replace"))
    # The captured output contains the token; drop it as soon as possible.
    captured[:] = b"\0" * len(captured)

    if not token:
        print("\nNo token found in the output of `claude setup-token`.", file=sys.stderr)
        return os.waitstatus_to_exitcode(status) or 1

    # The token was on screen, masked or not: clear the screen and scrollback.
    print(CLEAR_SCREEN, end="", flush=True)
    previous = env.load(env_file, {}).get("CLAUDE_CODE_OAUTH_TOKEN")
    env.upsert(env_file, "CLAUDE_CODE_OAUTH_TOKEN", token)
    print(f"Stored CLAUDE_CODE_OAUTH_TOKEN={env.mask(token)} in {env_file}")
    if previous and previous != token:
        print(
            "warning: this replaced the previous CLAUDE_CODE_OAUTH_TOKEN; the old token stays valid"
            " until you revoke it on claude.ai (settings, Claude Code)"
        )
    return 0


def _require(cfg: dict[str, str], key: str, hint: str) -> str | None:
    if not cfg.get(key):
        print(f"{key} is not set. {hint}", file=sys.stderr)
        return None
    return cfg[key]


def create_github_token(env_file: Path) -> int:
    """Create a fine-grained PAT via GitHub's pre-filled form and store it."""
    cfg = env.load(env_file, os.environ)
    org = _require(cfg, "LAB_GH_ORG", "Set it in .env or the environment.")
    return github_auth.create_pat(env_file, org, datetime.now(timezone.utc)) if org else 1


def check_github(env_file: Path, sandbox: Path) -> int:
    """Run spike S5 against GitHub and print one line per step."""
    cfg = env.load(env_file, os.environ)
    if not _require(cfg, "LAB_GH_ORG", "Set it in .env or the environment."):
        return 1
    if not _require(cfg, "GH_TOKEN", "Run `python3 lab.py tokens github` first."):
        return 1

    rows = github.check_s5(cfg, sandbox)
    width = max(len(step) for step, _, _ in rows)
    for step, ok, detail in rows:
        print(f"{step:<{width}}  {'ok' if ok else 'FAIL'}  {detail}".rstrip())
    return 0 if all(ok for _, ok, _ in rows) else 1
