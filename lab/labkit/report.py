"""Comparison report over the results file.

Rows come from `results.read` (strings, empty cell = missing) or straight
from a runner (numbers, None). Everything here treats both alike.

The output is shown live, so each table stays within a ~130-column
terminal: the first and third tables repeat mode, model and effort as
section headings instead of columns; the first shows only the most telling
metrics, and the third puts min–max on a line of its own.
"""

import statistics

from . import results

KEY = ("mode", "model", "effort", "profile", "prompt")
NON_NUMERIC = {
    "timestamp", "test", "mode", "model", "effort", "profile", "prompt", "status",
    "goal_ok", "assess_ok", "assess_failed", "plugin_versions", "mcp_connected",
}  # fmt: skip
NUMERIC = tuple(f for f in results.FIELDS if f not in NON_NUMERIC)
# Columns of the summary table, in order, after profile/prompt/n/ok/goal.
SHOWN = ("ctx_start", "ctx_growth", "tokens_out", "cache_read", "limit_used")
# The four kinds of tokens a run uses; their sum is `tokens_total`.
TOKEN_PARTS = ("tokens_in", "cache_write", "cache_read", "tokens_out")
# Columns of the token table, after profile/prompt/n: (field, header).
TOKEN_COLUMNS = (
    ("tokens_in", "input"), ("cache_write", "cache write"), ("cache_read", "cache read"),
    ("tokens_out", "output"), ("tokens_total", "total"), ("api_equiv_usd", "API-equiv"),
)  # fmt: skip
MISSING = "–"


def _num(value) -> float | int | None:
    """A number from a cell or a value; None for empty or unparsable."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return int(x) if x.is_integer() else x


def _is_true(value) -> bool:
    return str(value).strip().lower() == "true"


def _median(values: list):
    m = statistics.median(values)
    return int(m) if float(m).is_integer() else m


def _stats(values: list) -> dict | None:
    """median/min/max of the non-empty values; None when there are none."""
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"median": _median(values), "min": min(values), "max": max(values)}


def _limit_used(row: dict) -> float | int | None:
    start = _num(row.get("limit_5h_start_pct"))
    end = _num(row.get("limit_5h_end_pct"))
    return None if start is None or end is None else end - start


def _tokens_total(row: dict) -> float | int | None:
    """input + cache write + cache read + output of one run; None if a part is missing."""
    parts = [_num(row.get(f)) for f in TOKEN_PARTS]
    return None if None in parts else sum(parts)


def _group_key(row: dict, keys: tuple) -> tuple:
    return tuple(str(row.get(k) or "") for k in keys)


def _grouped(rows: list[dict], keys: tuple) -> list[tuple[tuple, list[dict]]]:
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(_group_key(r, keys), []).append(r)
    return sorted(groups.items())


def summarize(rows: list[dict]) -> list[dict]:
    """One dict per (mode, model, effort, profile, prompt), sorted by that key.

    A row without an effort has the effort "" here.

    Besides the key: `n` runs, `ok` (status ok), `goal_ok` (goal_ok
    true), `assess` ("<passed>/<assessed>", "" if no run was assessed),
    and for every numeric field plus `limit_used` (5-hour limit end minus
    start) and `tokens_total` (the four token kinds added up per run) a
    {"median", "min", "max"} dict, or None if no run has a value.
    `mcp_connected` is the range of MCP servers connected at start
    ("4/13" or "4/13–9/13"), "" if no run recorded it.
    """
    out = []
    for key, members in _grouped(rows, KEY):
        g = dict(zip(KEY, key))
        g["n"] = len(members)
        g["ok"] = sum(1 for r in members if str(r.get("status")) == "ok")
        g["goal_ok"] = sum(1 for r in members if _is_true(r.get("goal_ok")))
        g["assess"] = _assess(members)
        for field in NUMERIC:
            g[field] = _stats([_num(r.get(field)) for r in members])
        g["limit_used"] = _stats([_limit_used(r) for r in members])
        g["tokens_total"] = _stats([_tokens_total(r) for r in members])
        g["mcp_connected"] = _mcp_range(members)
        out.append(g)
    return out


def _assess(rows: list[dict]) -> str:
    """"<passed>/<assessed>" over the rows with an assessment result; "" if none has one."""
    assessed = [r for r in rows if str(r.get("assess_ok")).strip().lower() in ("true", "false")]
    if not assessed:
        return ""
    return f"{sum(1 for r in assessed if _is_true(r['assess_ok']))}/{len(assessed)}"


def _median_of(rows: list[dict], field: str):
    s = _stats([_num(r.get(field)) for r in rows])
    return None if s is None else s["median"]


def starting_points(rows: list[dict]) -> list[dict]:
    """Per (profile, model, effort): where each kind of context starts and grows.

    `model` is "<model> / <effort>" for a test with an effort, else the model.

    Medians of ctx_start/ctx_growth for interactive and for headless runs,
    and of sub_ctx_start_max/sub_ctx_growth_max for subagents (taken from
    every run of the pair, since subagents run inside either mode).
    """
    out = []
    for (profile, model, effort), members in _grouped(rows, ("profile", "model", "effort")):
        point = {"profile": profile, "model": _with_effort(model, effort)}
        for mode in ("interactive", "headless"):
            of_mode = [r for r in members if str(r.get("mode")) == mode]
            point[f"{mode}_ctx_start"] = _median_of(of_mode, "ctx_start")
            point[f"{mode}_ctx_growth"] = _median_of(of_mode, "ctx_growth")
        point["sub_ctx_start_max"] = _median_of(members, "sub_ctx_start_max")
        point["sub_ctx_growth_max"] = _median_of(members, "sub_ctx_growth_max")
        out.append(point)
    return out


def _with_effort(model: str, effort: str) -> str:
    return f"{model} / {effort}" if effort else model


def _mcp_range(members: list[dict]) -> str:
    """MCP servers connected at start over a group's runs: "4/13", "4/13–9/13", or ""."""
    values = set()
    for r in members:
        connected, _, total = str(r.get("mcp_connected") or "").partition("/")
        if connected.isdigit() and total.isdigit() and int(total) > 0:
            values.add((int(connected) / int(total), int(connected), int(total)))
    if not values:
        return ""
    low, high = min(values), max(values)
    text = f"{low[1]}/{low[2]}"
    return text if low == high else f"{text}–{high[1]}/{high[2]}"


def _section(g: dict) -> str:
    """Section heading of a summary group: mode / model, plus / effort if set."""
    return f"{g['mode']} / {_with_effort(g['model'], g['effort'])}"


def _fmt(value) -> str:
    """Short number: 999, 31.1k, 450k, 1.2M (rounded, so 999,999 is 1.0M)."""
    if value is None:
        return MISSING
    sign, size = ("-" if value < 0 else ""), abs(value)
    if round(size) < 1000:
        return f"{sign}{round(size)}"
    if round(size / 1000, 1) < 100:
        return f"{sign}{size / 1000:.1f}k"
    if round(size / 1000) < 1000:
        return f"{sign}{round(size / 1000)}k"
    return f"{sign}{size / 1_000_000:.1f}M"


def _usd(value) -> str:
    """API-equivalent cost: `$0.009` (three decimals, so small runs do not show as 0)."""
    return MISSING if value is None else f"${value:.3f}"


def _cell(stats: dict | None, n: int) -> str:
    """Median, with (min–max) next to it only for groups of several runs."""
    if stats is None:
        return MISSING
    text = _fmt(stats["median"])
    if n > 1:
        text += f" ({_fmt(stats['min'])}–{_fmt(stats['max'])})"
    return text


def _table(header: list[str], rows: list[list[str]], left: int, sep: str = "  ") -> list[str]:
    """Fixed-width lines; the first `left` columns left-aligned, rest right."""
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    lines = []
    for r in [header, *rows]:
        cells = [c.ljust(w) if i < left else c.rjust(w) for i, (c, w) in enumerate(zip(r, widths))]
        lines.append(sep.join(cells).rstrip())
    return lines


def _sectioned(header: list[str], body: list[tuple[str, list[str] | str]], left: int) -> list[str]:
    """A table with a `[section]` line before each new section of `body`."""
    # One column grid for the whole table so sections line up.
    # Single-space gaps: the range cells are wide and the table must fit ~130 columns.
    table = _table(header, [cells for _, cells in body if not isinstance(cells, str)], left=left, sep=" ")
    lines = [table[0], "-" * len(table[0])]
    previous = None
    rows = iter(table[1:])
    for section, cells in body:
        # A string instead of cells is a note line under the previous row, outside the grid.
        line = f"  {cells}" if isinstance(cells, str) else next(rows)
        if section != previous:
            if previous is not None:
                lines.append("")
            lines.append(f"[{section}]")
            previous = section
        lines.append(line)
    return lines


def _summary_lines(rows: list[dict]) -> list[str]:
    body = []
    for g in summarize(rows):
        section = _section(g)
        body.append((section, [
            g["profile"], g["prompt"], str(g["n"]), str(g["ok"]), str(g["goal_ok"]),
            *(_cell(g[f], g["n"]) for f in SHOWN),
        ]))  # fmt: skip
        if g["assess"]:
            body.append((section, f"assessment passed: {g['assess']} of the runs that reached the goal"))
        if g["mcp_connected"]:
            body.append((section, f"MCP servers connected at start: {g['mcp_connected']}"))
    return _sectioned(["profile", "prompt", "n", "ok", "goal", *SHOWN], body, left=2)


def _token_lines(rows: list[dict]) -> list[str]:
    """Token table: medians per group, and for n > 1 their min–max on the line below.

    In one line, "median (min–max)" for six columns would not fit 130 columns.
    """
    body = []
    for g in summarize(rows):
        section = _section(g)
        fmts = [_usd if f == "api_equiv_usd" else _fmt for f, _ in TOKEN_COLUMNS]
        stats = [g[f] for f, _ in TOKEN_COLUMNS]
        body.append((section, [
            g["profile"], g["prompt"], str(g["n"]),
            *(MISSING if s is None else fmt(s["median"]) for s, fmt in zip(stats, fmts)),
        ]))  # fmt: skip
        if g["n"] > 1:
            body.append((section, [
                "", "", "", *("" if s is None else f"{fmt(s['min'])}–{fmt(s['max'])}" for s, fmt in zip(stats, fmts)),
            ]))  # fmt: skip
    return _sectioned(["profile", "prompt", "n", *(h for _, h in TOKEN_COLUMNS)], body, left=2)


def _starting_lines(rows: list[dict]) -> list[str]:
    cols = ("ctx_start", "ctx_growth")
    groups = ("interactive", "headless", "subagent")
    keys = [
        *(f"{m}_{c}" for m in ("interactive", "headless") for c in cols),
        "sub_ctx_start_max", "sub_ctx_growth_max",
    ]  # fmt: skip
    body = [[p["profile"], p["model"], *(_fmt(p[k]) for k in keys)] for p in starting_points(rows)]
    # Two header lines: the context kind spans its start and growth columns.
    sub = ["profile", "model", *("start", "growth") * 3]
    table = _table(sub, body, left=2)
    widths = [max(len(c) for c in col) for col in zip(sub, *body)]
    top = [" " * widths[0], " " * widths[1]]
    for i, name in enumerate(groups):
        top.append(name.rjust(widths[2 + 2 * i] + 2 + widths[3 + 2 * i]))
    return ["  ".join(top).rstrip(), *table]


def render(rows: list[dict]) -> str:
    """The three report tables as one string."""
    if not rows:
        return "No results yet."
    parts = [
        "Runs per mode, model, effort, profile and prompt: median, then min–max when n > 1",
        *_summary_lines(rows),
        "",
        "Starting point and growth per context: median, tokens",
        *_starting_lines(rows),
        "",
        "Token consumption per run, total = input + cache write + cache read + output:",
        "median, min–max on the line below when n > 1; API-equiv = the run's cost at API prices, USD",
        *_token_lines(rows),
    ]
    return "\n".join(parts)
