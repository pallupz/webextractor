"""Build id for this server: the released version plus the commit actually running.

These run as editable checkouts, so the packaging version alone cannot tell a
deployed box apart from a laptop; the commit can. The version is read from the
checkout's own pyproject.toml when there is one, because installed metadata is
frozen at install time and would keep reporting the old number after a pull.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from importlib.metadata import PackageNotFoundError, version as _dist_version

_DIST = "webextract"
_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _released() -> str:
    """Version from the source tree if we are in one, else installed metadata."""
    pyproject = _ROOT / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        project = re.search(r"^\[project\]$(.*?)(?=^\[|\Z)", text, re.M | re.S)
        if project:
            found = re.search(r'^version\s*=\s*"([^"]+)"', project.group(1), re.M)
            if found:
                return found.group(1)
    try:
        return _dist_version(_DIST)
    except PackageNotFoundError:
        return "0+unknown"


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def _commit() -> str | None:
    """Short commit of the checkout this package is imported from, if it is one.

    Deliberately not `git describe`: the release number already comes from
    pyproject, and a tag at HEAD would just repeat it and hide the commit.
    """
    sha = _git("rev-parse", "--short", "HEAD")
    if not sha:
        return None
    return f"{sha}-dirty" if _git("status", "--porcelain") else sha


def build_id() -> str:
    commit = _commit()
    return f"{_released()}+g{commit}" if commit else _released()


__version__ = build_id()
