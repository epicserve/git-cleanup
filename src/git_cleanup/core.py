"""Reusable branch-data pipeline: everything needed to analyze a repo's
branches with no UI attached.

This is the entry point for programmatic consumers (e.g. a CI job that
reports which branches each engineer should delete or archive):

    scan = scan_repo(config)
    recommended = planner.recommend_actions(scan.branches, archive_age_days=90)
    # group by branch.author_email for a per-engineer report
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from git_cleanup import gitops, planner
from git_cleanup.config import Config
from git_cleanup.models import BranchInfo
from git_cleanup.trackers import get_tracker


@dataclass(frozen=True)
class RepoScan:
    branches: list[BranchInfo]
    default_branch: str
    current_branch: str | None
    user_email: str
    issues_found: int  # how many tracker issues were successfully looked up


def scan_repo(
    config: Config,
    *,
    fetch: bool = True,
    cwd: Path | None = None,
) -> RepoScan:
    """Gather every local and remote branch with git + issue-tracker data.

    Raises GitError on git failures; tracker failures degrade to git-only
    data (a warning goes to stderr, the scan still succeeds).
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

    return RepoScan(
        branches=branches,
        default_branch=default,
        current_branch=current,
        user_email=user_email,
        issues_found=issues_found,
    )
