"""Data models shared across git-cleanup."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class IssueState(StrEnum):
    DONE = "done"
    OPEN = "open"
    UNKNOWN = "unknown"


class Action(StrEnum):
    KEEP = "keep"
    DELETE = "delete"
    DELETE_LOCAL = "delete-local"
    ARCHIVE = "archive"


class WorktreeAction(StrEnum):
    """Worktree actions are worktree-only: removing one never deletes its
    branch, which stays the Branches tab's business."""

    KEEP = "keep"
    REMOVE = "remove"


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
    # appended last so every existing positional construction stays valid
    worktree_path: Path | None = None

    @property
    def has_worktree(self) -> bool:
        return self.worktree_path is not None

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


@dataclass
class WorktreeInfo:
    """A linked or main worktree, joined to the branch it has checked out.

    Staleness comes from the branch's commit date, not the directory's mtime: a
    directory's mtime only moves when a direct child is added, removed, or
    renamed, so editing a file inside a worktree never bumps its root. Using the
    branch date is zero extra I/O, has no missing-directory failure mode, and
    makes this Age column mean the same thing as the Branches one.
    """

    path: Path
    head: str | None = None
    branch: str | None = None  # full refname
    bare: bool = False
    detached: bool = False
    locked: bool = False
    lock_reason: str = ""
    prunable: bool = False
    prune_reason: str = ""
    is_main: bool = False
    is_current: bool = False
    dirty_count: int | None = None  # None when git could not look
    branch_info: BranchInfo | None = None

    @property
    def name(self) -> str:
        """Decision and row key: worktree paths are unique within a repo."""
        return str(self.path)

    @property
    def short_branch(self) -> str | None:
        if self.branch is None:
            return None
        return self.branch.removeprefix("refs/heads/")

    @property
    def is_missing(self) -> bool:
        """The directory is gone; git keeps the bookkeeping until pruned."""
        return self.prunable

    @property
    def is_dirty(self) -> bool:
        return bool(self.dirty_count)

    @property
    def needs_force(self) -> bool:
        """`git worktree remove` refuses modified *or* untracked files."""
        return self.is_dirty

    @property
    def removable(self) -> bool:
        """git cannot remove the main worktree or the one you are standing in,
        and a locked worktree would need -f -f, which we never pass."""
        return not (self.is_main or self.is_current or self.locked)

    @property
    def age_days(self) -> int | None:
        return self.branch_info.age_days if self.branch_info else None

    @property
    def merged(self) -> bool:
        return self.branch_info.merged if self.branch_info else False

    @property
    def issue_done(self) -> bool:
        return self.branch_info.issue_done if self.branch_info else False

    def is_mine(self, email: str) -> bool:
        return self.branch_info.is_mine(email) if self.branch_info else False


@dataclass(frozen=True)
class Outcome:
    """The user's confirmed decisions. Lives here rather than in tui.py so that
    cli and core can import it without dragging textual into the import graph."""

    branches: list[tuple[BranchInfo, Action]] = field(default_factory=list)
    worktrees: list[tuple[WorktreeInfo, WorktreeAction]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.branches or self.worktrees)
