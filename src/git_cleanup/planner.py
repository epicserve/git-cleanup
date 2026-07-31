"""Pure selection logic: build BranchInfo records and group cleanup candidates.

No I/O here — everything is unit-testable with plain data.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from git_cleanup.gitops import RawRef, RawStash, RawWorktree
from git_cleanup.models import (
    Action,
    BranchInfo,
    IssueInfo,
    StashInfo,
    WorktreeAction,
    WorktreeInfo,
)


def build_branches(
    refs: Sequence[RawRef],
    merged_names: set[str],
    *,
    current: str | None,
    default: str,
    protected: frozenset[str],
) -> list[BranchInfo]:
    """Merge local + remote refs by short name into BranchInfo records."""
    by_name: dict[str, dict[str, RawRef]] = {}
    for ref in refs:
        slot = "remote" if ref.is_remote else "local"
        by_name.setdefault(ref.short_name, {})[slot] = ref

    branches: list[BranchInfo] = []
    for name, slots in sorted(by_name.items()):
        local = slots.get("local")
        remote = slots.get("remote")
        primary = local or remote
        assert primary is not None
        # merged if the relevant ref tip is reachable from origin/<default>;
        # on divergence, require the side we'd delete to be merged
        merged = all(
            ref.refname in merged_names for ref in (local, remote) if ref is not None
        )
        branches.append(
            BranchInfo(
                name=name,
                has_local=local is not None,
                has_remote=remote is not None,
                sha=primary.sha,
                author_name=primary.author_name,
                author_email=primary.author_email,
                committed_at=primary.committed_at,
                merged=merged,
                ahead=local.ahead if local else None,
                behind=local.behind if local else None,
                upstream_gone=local.upstream_gone if local else False,
                is_current=name == current,
                is_default=name == default,
                is_protected=name in protected,
            )
        )
    return branches


def attach_issues(
    branches: Iterable[BranchInfo],
    issues: dict[str, IssueInfo],
) -> None:
    for branch in branches:
        if branch.issue_key is None:
            continue
        branch.issue = issues.get(branch.issue_key)


def extract_keys(branches: Iterable[BranchInfo], extract_key) -> list[str]:
    keys = []
    for branch in branches:
        branch.issue_key = extract_key(branch.name)
        if branch.issue_key:
            keys.append(branch.issue_key)
    return keys


_SORT_KEYS = {
    "branch": lambda b: b.name.lower(),
    "local": lambda b: b.has_local,
    "remote": lambda b: b.has_remote,
    "worktree": lambda b: b.has_worktree,
    "sync": lambda b: (b.ahead or 0, b.behind or 0),
    "author": lambda b: b.author_name.lower(),
    "age": lambda b: b.age_days,
    "merged": lambda b: b.merged,
    "issue": lambda b: (b.issue_key or "").lower(),
    "status": lambda b: b.issue.status.lower() if b.issue else "",
}
SORT_COLUMNS = tuple(_SORT_KEYS)
DEFAULT_SORT = "branch"


def parse_sort(spec: str) -> list[tuple[str, bool]]:
    """Parse a sort spec like '-age,status,author' into (column, descending) pairs."""
    fields: list[tuple[str, bool]] = []
    for raw in spec.split(","):
        raw = raw.strip()
        if not raw:
            continue
        descending = raw.startswith("-")
        name = raw.removeprefix("-").lower()
        if name == "name":
            name = "branch"
        if name not in _SORT_KEYS:
            raise ValueError(
                f"unknown sort column {name!r} (choose from: {', '.join(SORT_COLUMNS)})"
            )
        fields.append((name, descending))
    return fields


def format_sort(fields: Sequence[tuple[str, bool]]) -> str:
    """Inverse of parse_sort: [('age', True), ('author', False)] -> '-age,author'."""
    return ",".join(("-" if descending else "") + name for name, descending in fields)


def sort_branches(
    branches: Iterable[BranchInfo],
    fields: Sequence[tuple[str, bool]],
) -> list[BranchInfo]:
    """Multi-column sort; later columns break ties (stable sort, applied in reverse)."""
    result = list(branches)
    for name, descending in reversed(fields):
        result.sort(key=_SORT_KEYS[name], reverse=descending)
    return result


_BOOL_COLUMNS = ("mine", "merged", "local", "remote", "gone", "worktree")
_TEXT_COLUMNS = ("branch", "author", "issue", "status")
_AGE_TERM_RE = re.compile(r"^age(>=|<=|>|<)(\d+)([dmy]?)$")
_AGE_UNIT_DAYS = {"": 1, "d": 1, "m": 30, "y": 365}

type FilterTerm = tuple  # ("bool", name, want) | ("age", op, days) | ("text", col, needle, want)


def parse_filter(spec: str) -> list[FilterTerm]:
    """Parse a filter spec like 'mine,!merged,age>90,author=sam' into AND terms.

    Term forms:
      mine / merged / local / remote / gone / worktree   (prefix ! to negate)
      age>N, age<N, age>=N, age<=N            (N in days, or with d/m/y suffix)
      branch=X, author=X, issue=X, status=X   (case-insensitive substring; != excludes)
      anything else                           (substring match across all text columns)
    """
    terms: list[FilterTerm] = []
    for raw in spec.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if "<" in raw or ">" in raw:
            age_match = _AGE_TERM_RE.match(raw)
            if not age_match:
                raise ValueError(
                    f"bad filter term {raw!r} (expected age>N, age<N, age>=N or age<=N, "
                    "with N in days or a d/m/y suffix)"
                )
            op, number, unit = age_match.groups()
            terms.append(("age", op, int(number) * _AGE_UNIT_DAYS[unit]))
            continue
        if "=" in raw:
            col, _, needle = raw.partition("=")
            want = not col.endswith("!")
            col = col.rstrip("!").strip().lower()
            if col not in _TEXT_COLUMNS or not needle.strip():
                raise ValueError(
                    f"bad filter term {raw!r} (text columns: {', '.join(_TEXT_COLUMNS)})"
                )
            terms.append(("text", col, needle.strip(), want))
            continue
        want = not raw.startswith("!")
        name = raw.removeprefix("!").lower()
        if name in _BOOL_COLUMNS:
            terms.append(("bool", name, want))
        else:
            # bare word: substring match across all text columns
            terms.append(("text", "any", raw.removeprefix("!"), want))
    return terms


def _matches(b: BranchInfo, term: FilterTerm, my_email: str) -> bool:
    match term:
        case ("bool", name, want):
            value = {
                "mine": b.is_mine(my_email),
                "merged": b.merged,
                "local": b.has_local,
                "remote": b.has_remote,
                "gone": b.upstream_gone,
                "worktree": b.has_worktree,
            }[name]
            return value == want
        case ("age", op, days):
            age = b.age_days
            return {
                ">": age > days,
                "<": age < days,
                ">=": age >= days,
                "<=": age <= days,
            }[op]
        case ("text", col, needle, want):
            columns = {
                "branch": b.name,
                "author": f"{b.author_name} {b.author_email}",
                "issue": b.issue_key or "",
                "status": b.issue.status if b.issue else "",
            }
            haystack = " ".join(columns.values()) if col == "any" else columns[col]
            return (needle.lower() in haystack.lower()) == want
    raise AssertionError(f"unreachable: {term}")


def filter_branches(
    branches: Iterable[BranchInfo],
    terms: Sequence[FilterTerm],
    my_email: str,
) -> list[BranchInfo]:
    return [b for b in branches if all(_matches(b, t, my_email) for t in terms)]


def build_worktrees(
    raw_worktrees: Sequence[RawWorktree],
    branches: Iterable[BranchInfo],
    *,
    current_path: Path | None = None,
    dirty_counts: dict[Path, int | None] | None = None,
) -> list[WorktreeInfo]:
    """Join raw worktree records to BranchInfo by short branch name.

    Also back-fills BranchInfo.worktree_path so the Branches tab can show a WT
    indicator. First record wins: a branch is checked out at most once, but a
    broken entry can still name a branch that a live worktree also holds.

    Path comparison is lexical normpath only — the planner does no filesystem
    I/O, and both sides come from git already absolute.
    """
    by_branch = {b.name: b for b in branches}
    counts = dirty_counts or {}
    normalized_current = os.path.normpath(current_path) if current_path is not None else None

    worktrees: list[WorktreeInfo] = []
    for index, raw in enumerate(raw_worktrees):
        branch_info = by_branch.get(raw.short_branch) if raw.short_branch else None
        if branch_info is not None and branch_info.worktree_path is None:
            branch_info.worktree_path = raw.path
        worktrees.append(
            WorktreeInfo(
                path=raw.path,
                head=raw.head,
                branch=raw.branch,
                bare=raw.bare,
                detached=raw.detached,
                locked=raw.locked,
                lock_reason=raw.lock_reason,
                prunable=raw.prunable,
                prune_reason=raw.prune_reason,
                is_main=index == 0,  # git documents the main worktree as first
                is_current=(
                    normalized_current is not None
                    and os.path.normpath(raw.path) == normalized_current
                ),
                dirty_count=counts.get(raw.path),
                branch_info=branch_info,
            )
        )
    return worktrees


def recommend_worktree_actions(
    worktrees: Iterable[WorktreeInfo],
    *,
    for_email: str | None = None,
    include_all: bool = False,
) -> dict[str, WorktreeAction]:
    """Recommend REMOVE per worktree name (its path).

    Broken entries are recommended unconditionally: the directory is already
    gone, nothing is at risk, and the authorship of a vanished checkout is
    meaningless. Otherwise a worktree is recommended when its branch is merged
    or issue-done, under the same authorship gate as recommend_actions.

    Never recommends a dirty worktree — pre-marking must not set up a --force
    that discards uncommitted work. Never recommends one git cannot remove
    (main, current, locked), nor one whose branch is current, default, or
    protected: a `develop` worktree stays manually markable but is never
    pre-marked.
    """
    recommendations: dict[str, WorktreeAction] = {}
    for wt in worktrees:
        if not wt.removable:
            continue
        if wt.prunable:
            recommendations[wt.name] = WorktreeAction.REMOVE
            continue
        if wt.is_dirty:
            continue
        branch = wt.branch_info
        if branch is None or branch.is_current or branch.is_default or branch.is_protected:
            continue
        anyone = include_all or for_email is None
        if (branch.merged or branch.issue_done) and (anyone or branch.is_mine(for_email)):
            recommendations[wt.name] = WorktreeAction.REMOVE
    return recommendations


# git writes exactly "On <branch>: <msg>" or "WIP on <branch>: <sha> <subj>"
# (builtin/stash.c). Branch names can contain neither ':' nor a space, so [^:]+
# stops at the branch/message boundary while the message keeps every later ': '
# intact. Case is exact on purpose: re.I would also fire on an arbitrary subject
# that happens to begin "on ".
_STASH_SUBJECT_RE = re.compile(r"^(?:(?P<wip>WIP on)|On) (?P<branch>[^:]+): (?P<message>.*)$")
_DETACHED_SENTINEL = "(no branch)"


def parse_stash_subject(subject: str) -> tuple[str | None, str, bool]:
    """Split a reflog subject into (branch, message, wip).

    Returns (None, subject, False) for anything that is not one of git's four
    shapes: `git stash store -m X` writes X verbatim with no "On" prefix, and a
    subject we cannot decompose is better shown whole than silently mangled.
    "(no branch)" is a detached-HEAD sentinel, not a branch, so it maps to None.
    """
    match = _STASH_SUBJECT_RE.match(subject)
    if match is None:
        return None, subject, False
    branch = match["branch"]
    return (
        None if branch == _DETACHED_SENTINEL else branch,
        match["message"],
        bool(match["wip"]),
    )


def build_stashes(
    raw_stashes: Sequence[RawStash],
    *,
    file_counts: dict[str, int | None] | None = None,
) -> list[StashInfo]:
    """Turn raw stash records into StashInfo, preserving reflog order.

    Deliberately never sorts, in contrast to build_branches: reflog order is not
    date order — stash@{0} can be older than stash@{1} — so any reordering would
    put the numbers the user reads out of step with the selectors the executor
    acts on.
    """
    counts = file_counts or {}
    stashes: list[StashInfo] = []
    for raw in raw_stashes:
        branch, message, wip = parse_stash_subject(raw.subject)
        stashes.append(
            StashInfo(
                index=raw.index,
                selector=raw.selector,
                sha=raw.sha,
                created_at=raw.created_at,
                subject=raw.subject,
                branch=branch,
                message=message,
                wip=wip,
                parent_count=len(raw.parents),
                file_count=counts.get(raw.selector),
            )
        )
    return stashes


# There is deliberately no recommend_stash_actions: stashes are never pre-marked
# (see the comment where stash_actions is built in tui.py), so it could only ever
# return an empty dict.


def recommend_actions(
    branches: Iterable[BranchInfo],
    *,
    for_email: str | None = None,
    include_all: bool = False,
    archive_age_days: int,
) -> dict[str, Action]:
    """Recommend a non-keep action per branch name.

    DELETE: merged or issue-done, authored by `for_email` (any author when
    include_all is set or for_email is None — the team-report case).
    ARCHIVE: not deletable but older than archive_age_days.
    Protected, current, and default branches are never recommended.
    """
    recommendations: dict[str, Action] = {}
    for b in branches:
        if b.is_current or b.is_default or b.is_protected:
            continue
        anyone = include_all or for_email is None
        if b.cleanup_eligible and (anyone or b.is_mine(for_email)):
            recommendations[b.name] = Action.DELETE
        elif b.age_days >= archive_age_days:
            recommendations[b.name] = Action.ARCHIVE
    return recommendations
