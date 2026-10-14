"""Claude Code status line for the lab.

Runs as a script (`python3 statusline.py --save PATH`) inside Claude's
status-line hook, so it uses the standard library only. It reads the status
JSON from stdin, optionally saves it, and prints one summary line.
"""

import json
import sys


def _num(value) -> str:
    """Format a count; 1000 and up as `31.1k`, anything missing as `?`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "?"
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(int(value))


def _pct(value) -> str:
    return "?" if isinstance(value, bool) or not isinstance(value, (int, float)) else f"{value:g}"


def _get(data: dict, *keys):
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def _current_context(usage) -> int | float | None:
    """Tokens currently in the context window, or None if no field is a number."""
    if not isinstance(usage, dict):
        return None
    fields = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    values = [usage.get(f) for f in fields]
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(numbers) if numbers else None


def render(data: dict) -> str:
    window = _get(data, "context_window")
    if not isinstance(window, dict):
        window = {}
    return (
        f"ctx {_num(_current_context(window.get('current_usage')))}"
        f" ({_pct(window.get('used_percentage'))}%)"
        f" · in {_num(window.get('total_input_tokens'))}"
        f" out {_num(window.get('total_output_tokens'))}"
        f" · 5h {_pct(_get(data, 'rate_limits', 'five_hour', 'used_percentage'))}% used"
    )


def main(argv: list[str]) -> int:
    save = argv[argv.index("--save") + 1] if "--save" in argv[:-1] else None
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    if save:
        # Overwrite: the last render wins, so the file holds the final state.
        with open(save, "w", encoding="utf-8") as f:
            f.write(raw)
    print(render(data if isinstance(data, dict) else {}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
