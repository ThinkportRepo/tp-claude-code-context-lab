"""Test matrix, prompts, goal and profile settings.

Reads the static configuration under `lab/` and the prompt and goal files
in the lab root. Makes no network calls and writes nothing.
"""

import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "claude-haiku-5-5"
# Values of `claude --effort`; a test without one passes no --effort.
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# The prompt file of a test without one, relative to the lab root.
DEFAULT_PROMPT = "prompt-simple.md"
# In the prompt or model column: use the default (to set a later column alone).
DEFAULT_MARK = "-"
# The goal of every run, relative to the lab root.
GOAL_FILE = "prompt-goal.toml"
# Check type -> the keys it takes besides `type`. The types are fixed here,
# not shell commands in the file: a check runs with the lab's GH_TOKEN in reach.
GOAL_CHECKS = {"pages_live": {"wait_s"}, "homepage_is_pages": set()}
# Checks of the finished app, run once after the last turn; optional, relative to the lab root.
ASSESSMENT_FILE = "prompt-assessment.toml"
# Check type -> the keys it takes besides `type`. All run in headless Chrome.
ASSESSMENT_CHECKS = {"page_ok": set(), "reacts_to_clicks": set(), "browser_script": set()}
# The scenario's own test that browser_script runs, relative to the lab root.
ASSESSMENT_SCRIPT = "prompt-assessment.js"

# A test id names sandbox/<id>/, which every run wipes, and the repo lab-<id>,
# which teardown only finds as `lab-test*`; so: no path parts, and a fixed prefix.
_TEST_ID = re.compile(r"test[A-Za-z0-9_-]*")
_PROFILE_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


@dataclass(frozen=True)
class Test:
    id: str
    profile: str
    prompt: str
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class Goal:
    """The checks of the goal file; a run reaches its goal when all of them pass."""

    pages_live: bool
    homepage_is_pages: bool
    # How long to wait for a GitHub Pages build before deciding; 0 without pages_live.
    pages_wait_s: int = 0


@dataclass(frozen=True)
class Assessment:
    """The check types of the assessment file, in file order."""

    checks: tuple[str, ...]


def _profile_dir(lab_dir: Path, profile: str) -> Path:
    """Return lab/profiles/<profile>, rejecting names that leave that directory."""
    path = lab_dir / "profiles" / profile
    if not _PROFILE_NAME.fullmatch(profile) or not path.is_dir():
        raise ValueError(f"unknown profile {profile!r}")
    return path


def _lab_file(lab_root: Path, name: str) -> Path:
    """Return the file `name` names, relative to the lab root.

    Raises ValueError if it leaves the lab root (also through a symlink),
    _NotFound (a ValueError too) if it is not an existing file.
    """
    root = lab_root.resolve()
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root):
        raise ValueError(f"{name!r} is not inside the lab root {root}")
    if not path.is_file():
        raise _NotFound(f"{name!r} not found")
    return path


class _NotFound(ValueError):
    """A file named relative to the lab root does not exist."""


def _prompt_file(lab_root: Path, prompt: str) -> Path:
    """Return the file `prompt` names, relative to the lab root.

    Raises ValueError if it leaves the lab root (also through a symlink) or
    is not an existing file.
    """
    try:
        return _lab_file(lab_root, prompt)
    except _NotFound:
        raise ValueError(
            f"prompt file {prompt!r} not found; the prompt column takes a file path"
            f" relative to the lab root, e.g. {DEFAULT_PROMPT}"
        ) from None
    except ValueError as exc:
        raise ValueError(f"prompt file {exc}") from None


def load_matrix(path: Path, environ: Mapping[str, str]) -> list[Test]:
    """Parse matrix.conf: whitespace columns `id profile [prompt] [model] [effort]`.

    The matrix lives in `<lab root>/lab/`. Prompt: a file path relative to the
    lab root, kept as written; `-` or no column means DEFAULT_PROMPT. Model:
    the model column, else LAB_MODEL from `environ`, else DEFAULT_MODEL; `-`
    also means "not set", so a test can name an effort without a model.
    Effort: one of EFFORTS, or None if the column is absent. Profiles are
    looked up in `profiles/` next to the file. Ids must match
    `test[A-Za-z0-9_-]*`, profile names `[a-z0-9][a-z0-9-]*`.
    Raises ValueError("<path>:<line>: ...") for the first bad line.
    """
    lab_root = path.parent.parent
    fallback = environ.get("LAB_MODEL") or DEFAULT_MODEL
    tests: list[Test] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        columns = line.split()
        where = f"{path}:{lineno}"
        if len(columns) not in (2, 3, 4, 5):
            raise ValueError(
                f"{where}: expected 'id profile [prompt] [model] [effort]', got {len(columns)} columns"
            )
        test_id, profile, *rest = columns
        prompt = rest[0] if rest and rest[0] != DEFAULT_MARK else DEFAULT_PROMPT
        model = rest[1] if len(rest) > 1 and rest[1] != DEFAULT_MARK else fallback
        effort = rest[2] if len(rest) > 2 else None
        if not _TEST_ID.fullmatch(test_id):
            raise ValueError(
                f"{where}: test id {test_id!r} must start with 'test' followed by letters, digits, '_' or '-'"
            )
        try:
            _profile_dir(path.parent, profile)
            _prompt_file(lab_root, prompt)
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from None
        if effort is not None and effort not in EFFORTS:
            raise ValueError(f"{where}: unknown effort {effort!r} (known: {', '.join(EFFORTS)})")
        if test_id in seen:
            raise ValueError(f"{where}: duplicate test id {test_id!r}")
        seen.add(test_id)
        tests.append(Test(test_id, profile, prompt, model, effort))
    return tests


def prompt_text(lab_root: Path, prompt: str) -> str:
    """Return the text of the prompt file `prompt` (relative to the lab root), unchanged."""
    return _prompt_file(lab_root, prompt).read_text()


def load_followups(path: Path) -> list[str]:
    """Return the non-blank, non-comment lines of the follow-up prompt file."""
    lines = (line.strip() for line in path.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def profile_settings(lab_root: Path, profile: str) -> dict:
    """Return the parsed settings.json of `profile`."""
    return json.loads((_profile_dir(lab_root / "lab", profile) / "settings.json").read_text())


def profile_mcp_servers(lab_root: Path, profile: str) -> dict:
    """Return the user-scope MCP servers of `profile` from its optional mcp.json.

    The file holds `{"mcpServers": {"<name>": {...}, ...}}`; without it the
    profile has none ({}). Raises ValueError naming the file if it is not
    valid JSON of that shape.
    """
    path = _profile_dir(lab_root / "lab", profile) / "mcp.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from None
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict) or not all(isinstance(v, dict) for v in servers.values()):
        raise ValueError(f'{path}: expected {{"mcpServers": {{"<name>": {{...}}, ...}}}}')
    return servers


def load_goal(path: Path) -> Goal:
    """Parse the goal file: one or more `[[check]]` tables, each with a `type` from GOAL_CHECKS.

    `pages_live` (the Pages site answers 200) takes `wait_s`, an integer >= 0:
    how long to wait for the Pages build. `homepage_is_pages` (the repo's
    About -> Website field points to the Pages URL) takes nothing else.
    Each type may appear once. Raises ValueError("<path>: ...") if the file
    is missing or invalid; there is no default goal.
    """
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ValueError(f"{path}: not found; it sets the goal every run is checked against") from None
    found: dict[str, dict] = {}
    for where, check in _checks(path, text, GOAL_CHECKS):
        kind = check["type"]
        if "wait_s" in GOAL_CHECKS[kind]:
            wait_s = check.get("wait_s")
            if isinstance(wait_s, bool) or not isinstance(wait_s, int) or wait_s < 0:
                raise ValueError(f"{where}: {kind} needs wait_s, an integer of at least 0 (seconds)")
        found[kind] = check
    return Goal(
        pages_live="pages_live" in found,
        homepage_is_pages="homepage_is_pages" in found,
        pages_wait_s=found["pages_live"]["wait_s"] if "pages_live" in found else 0,
    )


def load_assessment(path: Path, lab_root: Path) -> Assessment | None:
    """Parse the assessment file: one or more `[[check]]` tables, each with a `type` from ASSESSMENT_CHECKS.

    The types take no other keys. `browser_script` runs ASSESSMENT_SCRIPT,
    which must exist in the lab root. Each type may appear once. None if
    the file does not exist: the assessment is optional. Raises
    ValueError("<path>: ...") if it is invalid.
    """
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    checks = []
    for where, check in _checks(path, text, ASSESSMENT_CHECKS):
        if check["type"] == "browser_script":
            try:
                _lab_file(lab_root, ASSESSMENT_SCRIPT)
            except _NotFound:
                raise ValueError(
                    f"{where}: browser_script needs {ASSESSMENT_SCRIPT} in the lab root"
                ) from None
            except ValueError as exc:
                raise ValueError(f"{where}: browser_script needs {ASSESSMENT_SCRIPT}: {exc}") from None
        checks.append(check["type"])
    return Assessment(tuple(checks))


def describe_assessment(assessment: Assessment) -> str:
    """The assessment's checks in one line, e.g. for `lab.py setup`."""
    return ", ".join(
        f"{c} ({ASSESSMENT_SCRIPT})" if c == "browser_script" else c for c in assessment.checks
    )


def _checks(path: Path, text: str, known: dict[str, set[str]]):
    """Yield ("<path>: check <n>", table) for each `[[check]]` table in the TOML `text`.

    Every table has a `type` from `known` and only the keys `known` allows
    for it; each type appears once. Raises ValueError("<path>: ...") otherwise.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path}: not valid TOML ({exc})") from None
    for key in data:
        if key != "check":
            raise ValueError(f"{path}: unknown key {key!r}; the file holds [[check]] tables only")
    checks = data.get("check")
    if not isinstance(checks, list) or not checks:
        raise ValueError(f"{path}: expected at least one [[check]] table")
    seen: set[str] = set()
    for n, check in enumerate(checks, start=1):
        where = f"{path}: check {n}"
        kind = check.get("type") if isinstance(check, dict) else None
        if not isinstance(kind, str) or kind not in known:
            raise ValueError(f"{where}: unknown type {kind!r} (known: {', '.join(known)})")
        for key in check:
            if key != "type" and key not in known[kind]:
                raise ValueError(f"{where}: unknown key {key!r} for type {kind!r}")
        if kind in seen:
            raise ValueError(f"{where}: duplicate type {kind!r}")
        seen.add(kind)
        yield where, check


def describe_goal(goal: Goal) -> str:
    """The goal's checks in one line, e.g. for `lab.py setup`."""
    parts = [f"pages_live (wait up to {goal.pages_wait_s} s)"] if goal.pages_live else []
    return ", ".join(parts + (["homepage_is_pages"] if goal.homepage_is_pages else []))
