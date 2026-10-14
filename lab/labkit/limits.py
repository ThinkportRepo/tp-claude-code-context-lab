"""Usage-limit gate: read the subscription's limits, refuse to run low.

There is no CLI command that reads the limit without an API call, so
`probe` makes one minimal Haiku call and reads its `rate_limit_event`.
"""

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from . import sandbox
from .runner import headless_argv

PROBE_PROMPT = "Reply with OK"
PROBE_MODEL = "claude-haiku-5-5"
PROBE_TIMEOUT_S = 120


def _utilization_pct(window) -> int | None:
    value = window.get("utilization") if isinstance(window, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value * 100)


def _limits(stream: str) -> dict | None:
    """Used percentages from the last `rate_limit_event` with a 5-hour window."""
    found = None
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "rate_limit_event":
            continue
        info = event.get("rate_limit_info")
        windows = info.get("unifiedWindows") if isinstance(info, dict) else None
        if not isinstance(windows, dict):
            continue
        five_hour = _utilization_pct(windows.get("five_hour"))
        if five_hour is not None:
            found = {"five_hour_pct": five_hour, "seven_day_pct": _utilization_pct(windows.get("seven_day"))}
    return found


def _result_error(stream: str) -> str:
    """The text of the last `result` event that reports an error, or ''."""
    found = ""
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result" and event.get("is_error"):
            found = str(event.get("result") or event.get("subtype") or "")
    return found


def _report_failure(detail: str, cfg: dict[str, str]) -> None:
    """Print why the probe failed, on stderr; never shows a token."""
    secrets = [cfg[k] for k in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN") if cfg.get(k)]
    print(f"warning: the usage-limit probe failed: {sandbox.first_line(detail, secrets) or 'no output'}",
          file=sys.stderr)


def probe(lab_root: Path, cfg: dict[str, str]) -> dict | None:
    """Current limit use as `{"five_hour_pct", "seven_day_pct"}`, or None.

    Runs in a throwaway config dir (no settings, so no plugins) with the
    lab's credentials. None if the call fails or reports no 5-hour window;
    `seven_day_pct` is None if only that window is missing. A failed call
    prints its reason (first stderr line, else the result event's error).
    """
    with tempfile.TemporaryDirectory(prefix="lab-probe-") as tmp:
        config_dir, work = Path(tmp) / "config", Path(tmp) / "work"
        config_dir.mkdir()
        work.mkdir()
        try:
            cp = subprocess.run(
                headless_argv(PROBE_PROMPT, PROBE_MODEL),
                cwd=work,
                env=sandbox.child_env(os.environ, lab_root, config_dir, cfg),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _report_failure(str(exc), cfg)
            return None
    if cp.returncode != 0:
        _report_failure(cp.stderr if cp.stderr.strip() else _result_error(cp.stdout), cfg)
        return None
    return _limits(cp.stdout)


def limit_line(probe: dict | None) -> str:
    """One line on how much of the 5-hour and 7-day limit is left."""
    if probe is None:
        return "5-hour limit: unknown (the probe call failed or reported no limit)"
    seven_day = probe.get("seven_day_pct")
    seven_left = "?" if seven_day is None else 100 - seven_day
    return f"5-hour limit: {100 - probe['five_hour_pct']} % left (7-day: {seven_left} % left)"


def gate(
    probe: dict | None,
    *,
    ask: Callable[[str], bool],
    is_tty: bool,
    min_remaining: int = 10,
) -> bool:
    """Print the limit; True if a run may start.

    Below `min_remaining` % of the 5-hour limit left, or with an unknown
    limit, a run needs an explicit yes. Without a terminal there is nobody
    to ask, so the answer is no.
    """
    left = None if probe is None else 100 - probe["five_hour_pct"]
    print(limit_line(probe))
    if left is not None and left >= min_remaining:
        return True
    if not is_tty:
        print("stopping: no terminal to confirm")
        return False
    if left is None:
        return ask("The 5-hour limit is unknown. Run anyway? [y/N]")
    return ask(f"Only {left} % of the 5-hour limit left. Run anyway? [y/N]")
