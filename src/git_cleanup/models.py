"""Data models shared across git-cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class IssueState(StrEnum):
    DONE = "done"
    OPEN = "open"
    UNKNOWN = "unknown"


class Action(StrEnum):
    KEEP = "keep"
    DELETE = "delete"
    DELETE_LOCAL = "delete-local"
    ARCHIVE = "archive"


@dataclass(frozen=True)
class IssueInfo:
    key: str
    summary: str
    status: str
    state: IssueState
    url: str


@dataclass
class BranchInfo:
    name: str
    has_local: bool
    has_remote: bool
    sha: str
    author_name: str
    author_email: str
    committed_at: datetime
    merged: bool
    ahead: int | None = None  # local vs upstream; None when no upstream
    behind: int | None = None
    upstream_gone: bool = False
    issue_key: str | None = None
    issue: IssueInfo | None = None
    is_current: bool = False
    is_default: bool = False
    is_protected: bool = False

    @property
    def age_days(self) -> int:
        return max(0, (datetime.now(UTC) - self.committed_at).days)

    @property
    def issue_done(self) -> bool:
        return self.issue is not None and self.issue.state is IssueState.DONE

    def is_mine(self, email: str) -> bool:
        return bool(email) and self.author_email.lower() == email.lower()

    @property
    def has_unpushed(self) -> bool:
        return bool(self.ahead)

    @property
    def has_both_refs(self) -> bool:
        """True when delete-local means something different from delete."""
        return self.has_local and self.has_remote

    @property
    def cleanup_eligible(self) -> bool:
        if self.is_current or self.is_default or self.is_protected:
            return False
        return self.merged or self.issue_done
