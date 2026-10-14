"""Test matrix, prompts and profile settings.

Reads the static configuration under `lab/` and the prompt files. Makes no
network calls and writes nothing.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "claude-haiku-5-5"

# Prompt name -> file, relative to the lab root. `well-defined` is the
# repo's own prompt file; both are used unchanged.
PROMPT_FILES = {
    "simple": Path("lab") / "prompts" / "simple.txt",
    "well-defined": Path("well-defined-prompt"),
}

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


def _profile_dir(lab_dir: Path, profile: str) -> Path:
    """Return lab/profiles/<profile>, rejecting names that leave that directory."""
    path = lab_dir / "profiles" / profile
    if not _PROFILE_NAME.fullmatch(profile) or not path.is_dir():
        raise ValueError(f"unknown profile {profile!r}")
    return path


def load_matrix(path: Path, environ: Mapping[str, str]) -> list[Test]:
    """Parse matrix.conf: whitespace columns `id profile prompt [model]`.

    Model per test: the model column, else LAB_MODEL from `environ`, else
    DEFAULT_MODEL. Profiles are looked up in `profiles/` next to the file.
    Ids must match `test[A-Za-z0-9_-]*`, profile names `[a-z0-9][a-z0-9-]*`.
    Raises ValueError("<path>:<line>: ...") for the first bad line.
    """
    fallback = environ.get("LAB_MODEL") or DEFAULT_MODEL
    tests: list[Test] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        columns = line.split()
        where = f"{path}:{lineno}"
        if len(columns) not in (3, 4):
            raise ValueError(f"{where}: expected 'id profile prompt [model]', got {len(columns)} columns")
        test_id, profile, prompt, *model = columns
        if not _TEST_ID.fullmatch(test_id):
            raise ValueError(
                f"{where}: test id {test_id!r} must start with 'test' followed by letters, digits, '_' or '-'"
            )
        try:
            _profile_dir(path.parent, profile)
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from None
        if prompt not in PROMPT_FILES:
            raise ValueError(f"{where}: unknown prompt {prompt!r} (known: {', '.join(PROMPT_FILES)})")
        if test_id in seen:
            raise ValueError(f"{where}: duplicate test id {test_id!r}")
        seen.add(test_id)
        tests.append(Test(test_id, profile, prompt, model[0] if model else fallback))
    return tests


def prompt_text(lab_root: Path, name: str) -> str:
    """Return the prompt text for `name`, unchanged."""
    try:
        relative = PROMPT_FILES[name]
    except KeyError:
        raise ValueError(f"unknown prompt {name!r} (known: {', '.join(PROMPT_FILES)})") from None
    return (lab_root / relative).read_text()


def load_followups(path: Path) -> list[str]:
    """Return the non-blank, non-comment lines of the follow-up prompt file."""
    lines = (line.strip() for line in path.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def profile_settings(lab_root: Path, profile: str) -> dict:
    """Return the parsed settings.json of `profile`."""
    return json.loads((_profile_dir(lab_root / "lab", profile) / "settings.json").read_text())
