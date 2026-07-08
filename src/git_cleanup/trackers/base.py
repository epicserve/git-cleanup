"""Issue tracker provider protocol.

New providers (GitHub, Linear, ...) implement this protocol and register
themselves in trackers/__init__.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from git_cleanup.models import IssueInfo


@runtime_checkable
class IssueTracker(Protocol):
    def extract_key(self, branch_name: str) -> str | None:
        """Return the issue key embedded in a branch name, if any."""
        ...

    def fetch_issues(self, keys: Sequence[str]) -> dict[str, IssueInfo]:
        """Fetch issue info for the given keys.

        Must degrade gracefully: on auth/network failure return {} (after
        warning the user once) rather than raising.
        """
        ...
