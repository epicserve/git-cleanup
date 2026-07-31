"""Reusable branch-data pipeline: everything needed to analyze a repo's
branches with no UI attached.

This is the entry point for programmatic consumers (e.g. a CI job that
reports which branches each engineer should delete or archive):

    scan = scan_repo(config)
    recommended = planner.recommend_actions(scan.branches, archive_age_days=90)
    # group by branch.author_email for a per-engineer report
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from git_cleanup import gitops, planner
from git_cleanup.config import Config
from git_cleanup.gitops import GitError
from git_cleanup.models import BranchInfo, StashInfo, WorktreeInfo
from git_cleanup.trackers import get_tracker


@dataclass(frozen=True)
class RepoScan:
    branches: list[BranchInfo]
    default_branch: str
    current_branch: str | None
    user_email: str
    issues_found: int  # how many tracker issues were successfully looked up
    worktrees: list[WorktreeInfo] = field(default_factory=list)
    stashes: list[StashInfo] = field(default_factory=list)


def scan_repo(
    config: Config,
    *,
    fetch: bool = True,
    cwd: Path | None = None,
) -> RepoScan:
    """Gather every local and remote branch with git + issue-tracker data,
    plus every worktree and every stash.

    Raises GitError on git failures; tracker failures degrade to git-only
    data (a warning goes to stderr, the scan still succeeds). Worktree- and
    stash-listing failures degrade the same way, to empty lists — a repo can be
    perfectly cleanable with an unreadable stash reflog.
    """
    if fetch:
        gitops.fetch_prune(cwd=cwd)

    default = gitops.get_default_branch(cwd=cwd)
    current = gitops.get_current_branch(cwd=cwd)
    user_email = gitops.get_user_email(cwd=cwd)

    refs = gitops.list_refs(cwd=cwd)
    merged = gitops.merged_ref_names(default, cwd=cwd)
    branches = planner.build_branches(
        refs,
        merged,
        current=current,
        default=default,
        protected=config.protected_branches,
    )

    issues_found = 0
    tracker = get_tracker(config)
    if tracker is not None:
        keys = planner.extract_keys(branches, tracker.extract_key)
        if keys:
            issues = tracker.fetch_issues(keys)
            planner.attach_issues(branches, issues)
            issues_found = len(issues)

    # after the tracker lookup: the join stores a reference to each BranchInfo,
    # and extract_keys/attach_issues mutate those same objects in place, so
    # WorktreeInfo.issue_done would see issue data either way — but building
    # worktrees last removes the aliasing question and survives a future
    # attach_issues that replaces objects instead of mutating them
    worktrees: list[WorktreeInfo] = []
    try:
        raw_worktrees = gitops.list_worktrees(cwd=cwd)
        dirty_counts = {
            raw.path: gitops.worktree_dirty_count(raw.path) for raw in raw_worktrees if not raw.bare
        }
        worktrees = planner.build_worktrees(
            raw_worktrees,
            branches,
            current_path=gitops.repo_root(cwd=cwd),
            dirty_counts=dirty_counts,
        )
    except GitError as exc:
        print(f"warning: could not list worktrees: {exc}", file=sys.stderr)

    stashes: list[StashInfo] = []
    try:
        raw_stashes = gitops.list_stashes(cwd=cwd)
        # stash_file_count swallows its own GitError into None, so one
        # unreadable stash costs a dash in a column, not the whole section
        file_counts = {
            raw.selector: gitops.stash_file_count(raw.selector, cwd=cwd) for raw in raw_stashes
        }
        stashes = planner.build_stashes(raw_stashes, file_counts=file_counts)
    except GitError as exc:
        print(f"warning: could not list stashes: {exc}", file=sys.stderr)

    return RepoScan(
        branches=branches,
        default_branch=default,
        current_branch=current,
        user_email=user_email,
        issues_found=issues_found,
        worktrees=worktrees,
        stashes=stashes,
    )
