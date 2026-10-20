"""The results file: one CSV row per run, schema fixed by FIELDS.

Rows are plain dicts. None and missing keys become empty cells; `read`
hands cells back as the strings `csv.DictReader` gives, and the report
converts what it needs.
"""

import csv
from pathlib import Path

# UTC; also the name of a run's results/<timestamp>/ directory.
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

FIELDS = (
    "timestamp", "test", "mode", "model", "effort", "profile", "prompt", "status",
    "goal_ok", "assess_ok", "assess_failed", "ctx_start", "ctx_end", "ctx_growth", "tokens_in",
    "tokens_out", "cache_read", "cache_write", "sub_calls",
    "sub_ctx_start_max", "sub_ctx_growth_max", "api_calls", "turns",
    "duration_s", "limit_5h_start_pct", "limit_5h_end_pct", "api_equiv_usd",
    "plugin_versions", "mcp_connected",
)  # fmt: skip


def _cell(key: str, value: object) -> str:
    if value is None:
        return ""
    if key == "plugin_versions" and isinstance(value, dict):
        # Sorted, so the same plugin set always serializes the same way.
        return ";".join(f"{name}@{value[name]}" for name in sorted(value))
    return str(value)


def _header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        return next(csv.reader(f), [])


def check_header(path: Path) -> None:
    """Raise ValueError if `path` exists with other columns than FIELDS.

    A missing or empty file is fine: `append` writes the header.
    """
    if path.exists() and path.stat().st_size > 0 and _header(path) != list(FIELDS):
        raise ValueError(
            f"{path} has other columns than this lab version writes; move it aside"
            f" (e.g. to {path.stem}-<date>{path.suffix}) and run again"
        )


def append(path: Path, row: dict) -> None:
    """Append one row; write the header first if the file is new or empty.

    A file written with other columns (an older lab version) is refused:
    its rows would no longer line up with the header.
    """
    unknown = sorted(set(row) - set(FIELDS))
    if unknown:
        raise ValueError(f"unknown result field(s): {', '.join(unknown)}")
    needs_header = not path.exists() or path.stat().st_size == 0
    check_header(path)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if needs_header:
            writer.writerow(FIELDS)
        writer.writerow(_cell(key, row.get(key)) for key in FIELDS)


def read(path: Path) -> list[dict]:
    """All rows as dicts of strings; a file that does not exist yet has none."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
