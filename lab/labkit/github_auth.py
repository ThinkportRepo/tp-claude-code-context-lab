"""The lab's GitHub credential: a fine-grained personal access token.

GitHub has no API to create one, so the user creates it in the browser on a
pre-filled form and pastes it here; it is stored in .env with its expiry.
"""

import getpass
import os
import secrets
import sys
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import env

_PAT_FORM_URL = "https://github.com/settings/personal-access-tokens/new"
_PAT_PREFIX = "github_pat_"
_PAT_TTL = timedelta(days=1)
# What the lab needs: create/delete repos, push, publish Pages. Workflows is
# needed because GitHub rejects pushes touching .github/workflows/* without it,
# and a run may legitimately deploy Pages through an Actions workflow.
_PERMISSIONS = {
    "administration": "write",
    "contents": "write",
    "pages": "write",
    "workflows": "write",
    "actions": "read",
    "metadata": "read",
}


def open_url(url: str) -> None:
    """Open url in LAB_BROWSER (if set) or the default browser; always print it."""
    print(url)
    browser = os.environ.get("LAB_BROWSER")
    if browser:
        webbrowser.get(browser + " %s").open(url)
    else:
        webbrowser.open(url)


def pat_url(org: str, suffix: str | None = None) -> str:
    """URL of GitHub's fine-grained token form, pre-filled for the lab.

    GitHub requires token names to be unique per user, so the name carries
    a random suffix (injectable for tests).
    """
    suffix = suffix or secrets.token_hex(2)
    params = {
        "name": f"Claude Code Context Lab {suffix}",
        "description": "Token for the Claude Code Context Lab: creates, pushes to and deletes lab-* repos.",
        "target_name": org,
        "expires_in": "1",  # days
        **_PERMISSIONS,
    }
    return f"{_PAT_FORM_URL}?{urllib.parse.urlencode(params)}"


def store_pat(env_file: Path, token: str, now: datetime) -> None:
    """Store a personal access token and its 24-hour expiry in env_file."""
    expires = now.astimezone(timezone.utc) + _PAT_TTL
    env.upsert(env_file, "GH_TOKEN", token)
    env.upsert(env_file, "GH_TOKEN_EXPIRES_AT", expires.isoformat())


def _form_checklist(org: str) -> list[str]:
    """What the form should show before generating; derived from _PERMISSIONS."""
    def names(level: str) -> str:
        return ", ".join(p.capitalize() for p, v in _PERMISSIONS.items() if v == level)

    return [
        f"  Resource owner: {org}",
        "  Expiration: 1 day",
        '  Repository access: All repositories (the org holds only lab repos)',
        f"  Read and write: {names('write')}",
        f"  Read-only: {names('read')}",
        "GitHub sometimes drops pre-filled values (for example after changing "
        "the resource owner) — check each before generating.",
    ]


def create_pat(env_file: Path, org: str, now: datetime, suffix: str | None = None) -> int:
    """Guide the user through creating a fine-grained PAT and store it.

    The form is pre-filled by pat_url; the token is pasted without echo.
    """
    print(f"Creating a fine-grained token for {org} (valid for 1 day).")
    print("warning: GitHub shows the new token on the page; don't share or record your screen.")
    open_url(pat_url(org, suffix))
    print("\nCheck the form against this list, then click Generate token:")
    print("\n".join(_form_checklist(org)))
    try:
        token = getpass.getpass("Paste the token (input is hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled. Nothing was stored.", file=sys.stderr)
        return 1
    if not token.startswith(_PAT_PREFIX):
        print(f"That is not a fine-grained token: it must start with {_PAT_PREFIX}. Nothing was stored.",
              file=sys.stderr)
        return 1
    store_pat(env_file, token, now)
    expires = now.astimezone(timezone.utc) + _PAT_TTL
    print(f"Stored GH_TOKEN={env.mask(token)} in {env_file}")
    print(f"Expires at {expires.isoformat()}")
    return 0
