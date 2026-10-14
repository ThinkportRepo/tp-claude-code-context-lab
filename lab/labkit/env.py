"""Reading and writing the lab's .env file.

The file holds secrets, so it is always written with mode 600 and replaced
atomically: a crash mid-write never leaves a half-written or world-readable
file behind.
"""

import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def load(path: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """Return the env file's values overlaid by environ (environment wins).

    Blank lines and `#` comments are ignored; one pair of surrounding single
    or double quotes is stripped from a value.
    """
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = _LINE.match(line)
            if not m:
                continue
            value = line[m.end():].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[m.group(1)] = value
    values.update(environ)
    return values


def file_problems(path: Path, lab_root: Path) -> list[str]:
    """Return what is wrong with the env file as a secret store (empty if fine).

    A missing file is not a problem. Two things are: being tracked by git
    (the secrets would be committed) and a mode other than 600.
    """
    if not path.exists():
        return []
    problems = []
    try:
        tracked = subprocess.run(
            ["git", "-C", str(lab_root), "ls-files", "--error-unmatch", str(path)],
            capture_output=True,
        ).returncode == 0
    except OSError:
        tracked = False  # no git binary: preflight reports that separately
    if tracked:
        problems.append(f"tracked by git: remove it from git with `git rm --cached {path.name}`")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        problems.append(f"mode is {mode:#o}, expected 0o600: fix with `chmod 600 {path.name}`")
    return problems


def upsert(path: Path, key: str, value: str) -> None:
    """Set key=value in the env file, keeping every other line as it is."""
    lines = path.read_text().splitlines() if path.exists() else []
    out, replaced = [], False
    for line in lines:
        m = _LINE.match(line)
        if m and m.group(1) == key:
            if not replaced:
                out.append(f"{key}={value}")
                replaced = True
            # Drop duplicate definitions of the same key.
            continue
        out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    _write(path, out)


def remove(path: Path, key: str) -> None:
    """Delete every definition of key from the env file, keeping other lines."""
    if not path.exists():
        return
    out = [
        line for line in path.read_text().splitlines()
        if not ((m := _LINE.match(line)) and m.group(1) == key)
    ]
    _write(path, out)


def _write(path: Path, out: list[str]) -> None:
    """Replace the env file atomically with the given lines, mode 600."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def mask(secret: str) -> str:
    """Show only enough of a secret to recognize it."""
    if len(secret) <= 16:
        return "***"
    return f"{secret[:14]}…{secret[-4:]} ({len(secret)} chars)"
