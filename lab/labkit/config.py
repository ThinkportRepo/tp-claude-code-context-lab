"""Test matrix, prompts and profile settings.

Reads the static configuration under `lab/` and the prompt files in the lab
root. Makes no network calls and writes nothing.
"""

import json
import re
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


def _profile_dir(lab_dir: Path, profile: str) -> Path:
    """Return lab/profiles/<profile>, rejecting names that leave that directory."""
    path = lab_dir / "profiles" / profile
    if not _PROFILE_NAME.fullmatch(profile) or not path.is_dir():
        raise ValueError(f"unknown profile {profile!r}")
    return path


def _prompt_file(lab_root: Path, prompt: str) -> Path:
    """Return the file `prompt` names, relative to the lab root.

    Raises ValueError if it leaves the lab root (also through a symlink) or
    is not an existing file.
    """
    root = lab_root.resolve()
    path = (root / prompt).resolve()
    if Path(prompt).is_absolute() or not path.is_relative_to(root):
        raise ValueError(f"prompt file {prompt!r} is not inside the lab root {root}")
    if not path.is_file():
        raise ValueError(
            f"prompt file {prompt!r} not found; the prompt column takes a file path"
            f" relative to the lab root, e.g. {DEFAULT_PROMPT}"
        )
    return path


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
