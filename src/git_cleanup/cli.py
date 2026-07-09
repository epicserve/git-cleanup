"""git-cleanup command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from git_cleanup import __version__, gitops, planner, tui, ui
from git_cleanup.config import Config, load_config
from git_cleanup.core import scan_repo
from git_cleanup.gitops import GitError
from git_cleanup.models import Action, BranchInfo


def _interactive() -> bool:
    return sys.stdin.isatty()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-cleanup",
        description=(
            "Interactively delete or archive git branches that are merged, "
            "done in your issue tracker, or stale."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be deleted/archived without changing anything",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="skip the initial 'git fetch --prune origin'",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="include branches authored by others in the cleanup prompts",
    )
    parser.add_argument(
        "--sort",
        default="branch",
        metavar="COLS",
        help=(
            "comma-separated sort columns, '-' prefix for descending; use the '=' form "
            f"for descending specs (e.g. --sort=-age,status); columns: "
            f"{', '.join(planner.SORT_COLUMNS)}"
        ),
    )
    parser.add_argument(
        "--filter",
        default="",
        metavar="TERMS",
        help=(
            "comma-separated filter terms, all must match; a bare word matches any text "
            "column (e.g. --filter brent); flags: mine, merged, local, remote, gone "
            "(prefix ! to negate); age>N/age<N (days, or 6m/1y); column=value substring "
            "match (branch, author, issue, status; != excludes). Quote specs containing "
            "> or ! (e.g. --filter 'mine,age>6m,status!=done')"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="path to config file (default: ~/.config/git-cleanup/config.toml)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _delete_local(branch: BranchInfo, *, dry_run: bool) -> bool:
    force = not branch.merged  # eligibility came from issue status; -d would refuse
    if dry_run:
        ui.dry_run_note(f"delete local branch {branch.name}")
        return True
    try:
        gitops.delete_local_branch(branch.name, force=force)
        ui.info(f"  deleted local [bold]{branch.name}[/bold]")
        return True
    except GitError as exc:
        ui.warn(f"could not delete local {branch.name}: {exc}")
        return False


def _delete_remote(branch: BranchInfo, *, dry_run: bool) -> bool:
    if dry_run:
        ui.dry_run_note(f"delete origin/{branch.name}")
        return True
    try:
        gitops.delete_remote_branch(branch.name)
        ui.info(f"  deleted [bold]origin/{branch.name}[/bold]")
        return True
    except GitError as exc:
        ui.warn(f"could not delete origin/{branch.name}: {exc}")
        return False


def _archive(branch: BranchInfo, *, dry_run: bool) -> bool:
    tag = f"archive/{branch.name}"
    if dry_run:
        ui.dry_run_note(f"tag {tag} at {branch.sha[:8]}, push tag, delete branch")
        return True
    if gitops.tag_exists(tag):
        ui.warn(f"tag {tag} already exists; skipping archive of {branch.name}")
        return False
    try:
        gitops.create_tag(tag, branch.sha)
        if branch.has_remote:
            gitops.push_tag(tag)
            gitops.delete_remote_branch(branch.name)
        if branch.has_local:
            gitops.delete_local_branch(branch.name, force=True)
        ui.info(f"  archived [bold]{branch.name}[/bold] → tag {tag}")
        return True
    except GitError as exc:
        ui.warn(f"could not archive {branch.name}: {exc}")
        return False


def _protect(branch: BranchInfo, current: str | None, default: str, config: Config) -> bool:
    """Final safety re-check before any destructive action."""
    return (
        branch.name == current
        or branch.name == default
        or branch.name in config.protected_branches
    )


def run(args: argparse.Namespace) -> int:
    try:
        sort_fields = planner.parse_sort(args.sort)
        filter_terms = planner.parse_filter(args.filter)
    except ValueError as exc:
        ui.warn(str(exc))
        return 2

    if not gitops.in_git_repo():
        ui.warn("not inside a git repository")
        return 1
    if not gitops.has_origin():
        ui.warn("this repository has no 'origin' remote")
        return 1

    config = load_config(args.config)

    if args.no_fetch:
        ui.info("[dim]skipping fetch (--no-fetch)[/dim]")
    with ui.console.status("Scanning branches..."):
        scan = scan_repo(config, fetch=not args.no_fetch)
    summary = f"[green]✓[/green] Found {len(scan.branches)} branches"
    if scan.issues_found:
        summary += f" · {scan.issues_found} issues looked up"
    ui.info(summary)

    branches = scan.branches
    if filter_terms:
        total = len(branches)
        branches = planner.filter_branches(branches, filter_terms, scan.user_email)
        ui.info(f"[dim]filter matched {len(branches)} of {total} branches[/dim]")
    branches = planner.sort_branches(branches, sort_fields)

    if not _interactive():
        ui.render_branch_table(branches)
        ui.warn("stdin is not a terminal; skipping interactive cleanup")
        return 0

    decisions = tui.run_tui(
        branches,
        my_email=scan.user_email,
        include_all=args.all,
        archive_age_days=config.archive_age_days,
        sort_fields=sort_fields,
        filter_spec=args.filter,
        dry_run=args.dry_run,
    )
    if decisions is None:
        ui.info("Aborted; no changes made.")
        return 0

    current, default = scan.current_branch, scan.default_branch
    deleted_local = deleted_remote = archived = 0
    for branch, action in decisions:
        if _protect(branch, current, default, config):
            continue
        if action is Action.DELETE:
            if branch.has_local and _delete_local(branch, dry_run=args.dry_run):
                deleted_local += 1
            if branch.has_remote and _delete_remote(branch, dry_run=args.dry_run):
                deleted_remote += 1
        elif action is Action.ARCHIVE:
            if _archive(branch, dry_run=args.dry_run):
                archived += 1

    prefix = "[cyan]\\[dry-run][/cyan] " if args.dry_run else ""
    ui.info(
        f"\n{prefix}Done: deleted {deleted_local} local, "
        f"{deleted_remote} remote, archived {archived}."
    )
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        ui.info("\nAborted.")
        return 130
    except GitError as exc:
        ui.warn(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
