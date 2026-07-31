"""Per-repo persisted view state (filter/sort) under $XDG_STATE_HOME."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path


def default_state_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "git-cleanup" / "state.json"


def _read_all(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def load_repo_state(repo_root: Path, path: Path | None = None) -> dict[str, str]:
    """The saved view for one repo, e.g. {'filter': 'mine', 'sort': '-age'}.

    Missing or corrupt state degrades to {} — persistence is never fatal.
    """
    if path is None:
        path = default_state_path()
    entry = _read_all(path).get(str(repo_root))
    return entry if isinstance(entry, dict) else {}


def save_repo_state(repo_root: Path, view: dict[str, str], path: Path | None = None) -> None:
    """Merge one repo's view into the state file.

    Best-effort: write failures are swallowed — this runs inside the TUI
    event loop and must never crash a session over a state file.
    """
    if path is None:
        path = default_state_path()
    data = _read_all(path)
    data[str(repo_root)] = view
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
    except OSError:
        pass
