"""Derive run metrics from Claude Code transcripts and `stream-json` output.

Reads only; makes no network calls. Never raises on missing, empty or
malformed data: unreadable files and lines are skipped, and metrics that
cannot be derived are `None`.

Transcript layout (inside a Claude config dir):
    projects/<slug>/<session>.jsonl                      main transcript
    projects/<slug>/<session>/subagents/agent-*.jsonl    one file per subagent
"""

import json
from collections.abc import Iterator
from pathlib import Path

COMPUTE_KEYS = (
    "ctx_start", "ctx_end", "ctx_growth", "api_calls", "turns", "tokens_in",
    "tokens_out", "cache_read", "cache_write", "api_equiv_usd", "sub_calls",
    "sub_ctx_start_max", "sub_ctx_growth_max",
)  # fmt: skip

# modelUsage field -> metric name (shared by transcripts and stream results)
USAGE_FIELDS = {
    "inputTokens": "tokens_in",
    "outputTokens": "tokens_out",
    "cacheReadInputTokens": "cache_read",
    "cacheCreationInputTokens": "cache_write",
}


SYNTHETIC_MODEL = "<synthetic>"
INPUT_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _entries(path: Path) -> Iterator[dict]:
    """Yield the JSON objects of a JSONL file, skipping anything unreadable."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    yield entry
    except OSError:
        return


def _num(value) -> int | float:
    """`value` if it is a real number, else 0 (bool is not a number here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return value


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _first_timestamp(path: Path) -> str | None:
    for entry in _entries(path):
        ts = entry.get("timestamp")
        if isinstance(ts, str):
            return ts
    return None


def main_transcripts(config_dir: Path) -> list[Path]:
    """Main transcripts `projects/*/*.jsonl`, oldest first.

    Ordered by the timestamp of each file's first entry that has one;
    files without any timestamp sort last. Subagent files live one level
    deeper and are not matched.
    """
    found = []
    for path in Path(config_dir).glob("projects/*/*.jsonl"):
        ts = _first_timestamp(path)
        found.append((ts is None, ts or "", path.name, path))
    return [item[-1] for item in sorted(found)]


def git_branches(config_dir: Path) -> set[str]:
    """Every `gitBranch` value in the main transcripts under `config_dir`.

    Claude Code records the branch of the repository it found for its cwd.
    With the sandbox's git boundary that is `HEAD`; the lab repo's branch
    here means the run saw the lab repo. Never raises; empty if none.
    """
    return {
        entry["gitBranch"]
        for path in main_transcripts(config_dir)
        for entry in _entries(path)
        if isinstance(entry.get("gitBranch"), str)
    }


def _context(usage: dict) -> int | float:
    """Prompt size of one API call: fresh + cache-read + cache-write input."""
    return (
        _num(usage.get("input_tokens"))
        + _num(usage.get("cache_read_input_tokens"))
        + _num(usage.get("cache_creation_input_tokens"))
    )


def _has_input_usage(usage) -> bool:
    """True if `usage` has at least one numeric input-side token field."""
    return isinstance(usage, dict) and any(
        isinstance(usage.get(k), (int, float)) and not isinstance(usage.get(k), bool)
        for k in INPUT_FIELDS
    )


def _call_contexts(entries) -> list[int | float]:
    """Context size per distinct API call, in order of first appearance.

    Claude Code logs one response several times (one entry per content
    block), all with the same `message.id`; the last entry of an id wins.
    Entries that are not API responses are skipped: Claude Code's own
    `"<synthetic>"` messages (API errors, usage-limit notices) carry an id
    and all-zero usage, and an entry with no numeric input-side usage
    field at all has nothing to measure. A real call with a zero field
    still counts.
    """
    by_id: dict[str, int | float] = {}
    for entry in entries:
        message = _dict(entry.get("message"))
        msg_id, usage = message.get("id"), message.get("usage")
        if entry.get("type") != "assistant" or not isinstance(msg_id, str):
            continue
        if message.get("model") == SYNTHETIC_MODEL or not _has_input_usage(usage):
            continue
        by_id[msg_id] = _context(usage)
    return list(by_id.values())


def _is_turn(entry: dict) -> bool:
    """A user prompt: `promptSource` set and not an internal notification."""
    source = entry.get("promptSource")
    return entry.get("type") == "user" and bool(source) and source != "system"


def _totals(entries) -> dict | None:
    """Usage and cost summed over processes, from `cost-state` entries.

    `cost-state` entries carry cumulative usage keyed by `startTime`; the
    last one per `startTime` is that key's total, and the keys of a run
    are added up. A resumed process (`claude -p --resume`) keeps the
    session's `startTime` and continues its cumulative usage, so its
    entries replace the earlier ones instead of adding a key: taking the
    last entry per `startTime` counts every turn exactly once, while a
    separate process adds its own key. `None` if there is no `cost-state`.
    """
    last: dict[str, dict] = {}
    for entry in entries:
        if entry.get("type") == "cost-state":
            last[repr(entry.get("startTime"))] = entry
    if not last:
        return None
    totals = {name: 0 for name in USAGE_FIELDS.values()}
    totals["api_equiv_usd"] = 0.0
    for state in last.values():
        totals["api_equiv_usd"] += _num(state.get("totalCostUSD"))
        for usage in _dict(state.get("modelUsage")).values():
            for field, name in USAGE_FIELDS.items():
                totals[name] += _num(_dict(usage).get(field))
    return totals


def compute(config_dir: Path) -> dict:
    """Run metrics from the transcripts under `config_dir`.

    Main thread = non-sidechain entries of the main transcripts. Synthetic
    assistant entries (`message.model == "<synthetic>"`) are not API calls
    and are ignored, in the main thread and in subagent files. Counts
    (`api_calls`, `turns`, `sub_calls`) are 0 when transcripts exist but
    contain none; everything else is `None` when its data is missing.
    """
    result = dict.fromkeys(COMPUTE_KEYS)
    mains = main_transcripts(config_dir)
    if not mains:
        return result

    entries = [e for path in mains for e in _entries(path)]
    main_thread = [e for e in entries if not e.get("isSidechain")]

    contexts = _call_contexts(main_thread)
    result["api_calls"] = len(contexts)
    result["turns"] = sum(_is_turn(e) for e in main_thread)
    if contexts:
        result["ctx_start"], result["ctx_end"] = contexts[0], contexts[-1]
        result["ctx_growth"] = contexts[-1] - contexts[0]

    totals = _totals(main_thread)
    if totals:
        result.update(totals)

    # One file per subagent: its own context starts fresh, so compare per file.
    sub_runs = []
    for main in mains:
        sub_dir = main.with_suffix("") / "subagents"
        for sub in sorted(sub_dir.glob("agent-*.jsonl")):
            sub_runs.append(_call_contexts(_entries(sub)))
    sub_runs = [run for run in sub_runs if run]
    result["sub_calls"] = sum(len(run) for run in sub_runs)
    if sub_runs:
        result["sub_ctx_start_max"] = max(run[0] for run in sub_runs)
        result["sub_ctx_growth_max"] = max(run[-1] - run[0] for run in sub_runs)
    return result


def _mcp_connected(servers) -> str:
    """"<connected>/<total>" for an init event's `mcp_servers`; "" without servers."""
    if not isinstance(servers, list) or not servers:
        return ""
    connected = sum(1 for s in servers if isinstance(s, dict) and s.get("status") == "connected")
    return f"{connected}/{len(servers)}"


def stream_info(stream_files: list[Path]) -> dict:
    """Facts from `stream-json` output, one file per `claude` process.

    `result_totals` sums, over distinct `session_id`s, the `modelUsage` of
    each session's last `result` event (files in the order given), and
    over models, with the same names `compute` uses for tokens. Usage in
    `result` events is cumulative per session: within one process, and
    also across `claude -p --resume <id>` processes, which keep the
    session id and continue its totals. So neither events nor files of
    one session are ever added up. A `result` without a string
    `session_id` is treated as its own session. Missing data is `None`.

    `mcp_connected` is "<connected>/<total>" over the `mcp_servers` of the
    first `system`/`init` event in the first file (the run's first turn):
    headless Claude Code does not wait for MCP servers before its first
    request, so this shows how many were there at the start. It is "" if
    that event is missing or lists no servers.
    """
    info = dict.fromkeys(("session_id", "limit_5h_end_pct", "denied", "result_totals"))
    info["mcp_connected"] = ""
    saw_init = False
    saw_result = False
    denied = False
    # last `result` modelUsage per session; a later result replaces an earlier one
    usage_by_session: dict[object, dict] = {}

    for index, path in enumerate(stream_files):
        for event in _entries(Path(path)):
            if index == 0 and not saw_init and event.get("type") == "system" and event.get("subtype") == "init":
                saw_init = True
                info["mcp_connected"] = _mcp_connected(event.get("mcp_servers"))
            elif event.get("type") == "result":
                saw_result = True
                denied = denied or bool(event.get("permission_denials"))
                session = event.get("session_id")
                if isinstance(session, str):
                    info["session_id"] = session
                else:
                    session = ("no-session-id", index)
                usage_by_session[session] = _dict(event.get("modelUsage"))
            elif event.get("type") == "rate_limit_event":
                windows = _dict(_dict(event.get("rate_limit_info")).get("unifiedWindows"))
                five_hour = _dict(windows.get("five_hour"))
                utilization = five_hour.get("utilization")
                if isinstance(utilization, (int, float)) and not isinstance(utilization, bool):
                    info["limit_5h_end_pct"] = round(utilization * 100)

    totals = {name: 0 for name in USAGE_FIELDS.values()}
    have_usage = False
    for model_usage in usage_by_session.values():
        for usage in model_usage.values():
            have_usage = True
            for field, name in USAGE_FIELDS.items():
                totals[name] += _num(_dict(usage).get(field))

    if saw_result:
        info["denied"] = denied
    if have_usage:
        info["result_totals"] = totals
    return info
