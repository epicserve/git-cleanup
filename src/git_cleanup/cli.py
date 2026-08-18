"""git-cleanup command-line entry point."""

from __future__ import annotations

import argparse
import sys
from functools import partial
from pathlib import Path

from git_cleanup import __version__, gitops, planner, state, tui, ui
from git_cleanup.config import Config, load_config
from git_cleanup.core import scan_repo
from git_cleanup.gitops import GitError
from git_cleanup.models import (
    RESTORE_ACTIONS,
    Action,
    BranchInfo,
    StashAction,
    StashInfo,
    WorktreeAction,
    WorktreeInfo,
)


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
        default=None,
        metavar="COLS",
        help=(
            "comma-separated sort columns, '-' prefix for descending; use the '=' form "
            f"for descending specs (e.g. --sort=-age,status); columns: "
            f"{', '.join(planner.SORT_COLUMNS)}; default: your last-used sort in this "
            f"repo, else {planner.DEFAULT_SORT}"
        ),
    )
    parser.add_argument(
        "--filter",
        default=None,
        metavar="TERMS",
        help=(
            "comma-separated filter terms, all must match; a bare word matches any text "
            "column (e.g. --filter brent); flags: mine, merged, local, remote, gone "
            "(prefix ! to negate); age>N/age<N (days, or 6m/1y); column=value substring "
            "match (branch, author, issue, status; != excludes); an empty value tests "
            "whether the column is set at all ('status=' for no status, 'status!=' for "
            "any). Quote specs containing > or ! (e.g. --filter 'mine,age>6m,status!=done'); "
            "default: your last-used filter in this repo"
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


def _remove_worktree(worktree: WorktreeInfo, *, dry_run: bool) -> bool:
    label = ui.format_worktree_path(worktree.path)
    force = worktree.needs_force
    if dry_run:
        ui.dry_run_note(
            f"remove worktree {label}" + (" (--force: uncommitted changes)" if force else "")
        )
        return True
    try:
        gitops.remove_worktree(worktree.path, force=force)
        ui.info(f"  removed worktree [bold]{label}[/bold]")
        return True
    except GitError as exc:
        ui.warn(f"could not remove worktree {label}: {exc}")
        return False


def _prune_worktrees(marked: int, *, dry_run: bool) -> int:
    """Clear broken worktree entries in one repo-wide call.

    Reports git's own --verbose list rather than the marked count: prune is
    repo-wide, so it may clear entries nobody marked.
    """
    if dry_run:
        try:
            pruned = gitops.prune_worktrees(dry_run=True)
        except GitError as exc:
            ui.warn(f"could not prune worktrees: {exc}")
            return 0
        ui.dry_run_note(
            f"run git worktree prune ({marked} marked; "
            f"clears {len(pruned)} broken worktree entries)"
        )
        return len(pruned)
    try:
        pruned = gitops.prune_worktrees()
    except GitError as exc:
        ui.warn(f"could not prune worktrees: {exc}")
        return 0
    for line in pruned:
        ui.info(f"  pruned [bold]{line}[/bold]")
    return len(pruned)


def _drop_stash(stash: StashInfo, *, dry_run: bool) -> bool:
    if dry_run:
        ui.dry_run_note(f"drop {stash.selector} ({stash.message})")
        return True
    try:
        gitops.drop_stash(stash.selector)
        ui.info(f"  dropped [bold]{stash.selector}[/bold] ({stash.sha[:8]})")
        return True
    except GitError as exc:
        ui.warn(f"could not drop {stash.selector}: {exc}")
        return False


def _restore_stash(stash: StashInfo, *, keep: bool, dry_run: bool) -> bool:
    verb, past = ("apply", "applied") if keep else ("pop", "popped")
    if dry_run:
        ui.dry_run_note(f"{verb} {stash.selector} onto the current branch")
        return True
    try:
        result = gitops.restore_stash(stash.selector, keep=keep)
    except GitError as exc:  # the selector is gone, or git could not run at all
        ui.warn(f"could not {verb} {stash.selector}: {exc}")
        return False
    if result.ok:
        kept = " (kept in the list)" if keep else ""
        ui.info(f"  {past} [bold]{stash.selector}[/bold]{kept}")
        return True
    if result.conflicted:
        ui.warn(
            f"{stash.selector} was applied with conflicts — resolve them, then drop it "
            f"by hand; it is still in the list. {result.detail}"
        )
    else:
        # deliberately not "nothing changed": a clobber refusal does leave the
        # tree untouched, but an untracked-file collision can fail after a
        # partial restore, and the exit code cannot tell the two apart
        ui.warn(f"could not {verb} {stash.selector} (still in the list): {result.detail}")
    return False


def _read_stash_diff(ref: str) -> str:
    """Patch text for the TUI's detail pane.

    Returns the failure as text rather than raising: tui.py has no git import so
    it cannot catch GitError, and the injected-capability contract there is
    "returns something printable, never raises".
    """
    try:
        return gitops.stash_patch(ref)
    except GitError as exc:
        return f"could not read this stash: {exc}"


def _protect(branch: BranchInfo, current: str | None, default: str, config: Config) -> bool:
    """Final safety re-check before any destructive action."""
    return (
        branch.name == current or branch.name == default or branch.name in config.protected_branches
    )


def _protect_worktree(worktree: WorktreeInfo) -> bool:
    """Final safety re-check before removing a worktree.

    Dirtiness is deliberately not re-checked: the review screen flagged it in
    red and the user confirmed, so --force is intended here.
    """
    return worktree.is_main or worktree.is_current or worktree.locked


def _stash_unchanged(stash: StashInfo) -> bool:
    """Final safety re-check, the stash counterpart of _protect_worktree.

    Reflog positions shift, and a TUI session can sit open while another terminal
    pops something. One rev-parse is all that stands between a stale decision and
    dropping work the user never looked at. Runs in dry-run too — it is read-only,
    and a dry run that reports a drop it would actually refuse is worse than
    useless.
    """
    try:
        return gitops.stash_sha(stash.selector) == stash.sha
    except GitError:
        return False


def run(args: argparse.Namespace) -> int:
    try:  # validate explicit flags before any git call
        if args.sort is not None:
            planner.parse_sort(args.sort)
        if args.filter is not None:
            planner.parse_filter(args.filter)
    except ValueError as exc:
        ui.warn(str(exc))
        return 2

    if not gitops.in_git_repo():
        ui.warn("not inside a git repository")
        return 1
    if not gitops.has_origin():
        ui.warn("this repository has no 'origin' remote")
        return 1

    root = gitops.repo_root().resolve()
    config = load_config(args.config, repo_root=root)

    # explicit flag > saved view (interactive runs only) > default
    interactive = _interactive()
    persisted = state.load_repo_state(root) if interactive else {}
    filter_spec = args.filter if args.filter is not None else persisted.get("filter", "")
    sort_spec = args.sort if args.sort is not None else persisted.get("sort", planner.DEFAULT_SORT)
    try:
        filter_terms = planner.parse_filter(filter_spec)
    except ValueError as exc:  # explicit flags were pre-validated: saved spec is at fault
        ui.warn(f"ignoring saved filter {filter_spec!r}: {exc}")
        filter_spec, filter_terms = "", []
    try:
        sort_fields = planner.parse_sort(sort_spec)
    except ValueError as exc:
        ui.warn(f"ignoring saved sort {sort_spec!r}: {exc}")
        sort_fields = planner.parse_sort(planner.DEFAULT_SORT)

    if args.no_fetch:
        ui.info("[dim]skipping fetch (--no-fetch)[/dim]")
    with ui.console.status("Scanning branches..."):
        scan = scan_repo(config, fetch=not args.no_fetch)
    summary = f"[green]✓[/green] Found {len(scan.branches)} branches"
    if scan.issues_found:
        summary += f" · {scan.issues_found} issues looked up"
    ui.info(summary)

    if not interactive:
        branches = scan.branches
        if filter_terms:
            total = len(branches)
            branches = planner.filter_branches(branches, filter_terms, scan.user_email)
            ui.info(f"[dim]filter matched {len(branches)} of {total} branches[/dim]")
        ui.render_branch_table(planner.sort_branches(branches, sort_fields))
        # a repo with no linked worktrees yields exactly one record (the main
        # one), which is not worth a table of its own
        if len(scan.worktrees) > 1:
            ui.render_worktree_table(scan.worktrees)
        # no >1 threshold here, unlike worktrees: zero stashes is genuinely zero
        if scan.stashes:
            ui.render_stash_table(scan.stashes)
        ui.warn("stdin is not a terminal; skipping interactive cleanup")
        return 0

    def persist_view(filter_spec: str, sort_spec: str) -> None:
        state.save_repo_state(root, {"filter": filter_spec, "sort": sort_spec})

    web_url = gitops.origin_web_url()
    compare_url = partial(gitops.compare_url, web_url, scan.default_branch) if web_url else None

    # the TUI gets every branch; it applies filter/sort itself, so filters
    # can be loosened in-session to reveal hidden branches
    outcome = tui.run_tui(
        scan.branches,
        my_email=scan.user_email,
        include_all=args.all,
        archive_age_days=config.archive_age_days,
        sort_fields=sort_fields,
        filter_spec=filter_spec,
        dry_run=args.dry_run,
        on_view_change=persist_view,
        compare_url=compare_url,
        worktrees=scan.worktrees,
        stashes=scan.stashes,
        stash_diff=_read_stash_diff,
    )
    if outcome is None:
        ui.info("Aborted; no changes made.")
        return 0

    # WORKTREES FIRST. `git branch -d/-D` refuses a branch checked out in any
    # worktree, so a branch marked delete on the Branches tab and its worktree
    # marked remove on the Worktrees tab only works in this order. _archive
    # deletes the local branch too, so it needs the same ordering.
    removed_worktrees = to_prune = 0
    for worktree, wt_action in outcome.worktrees:
        if wt_action is not WorktreeAction.REMOVE or _protect_worktree(worktree):
            continue
        if worktree.prunable:
            to_prune += 1  # batched: `worktree remove` can't touch a missing dir
        elif _remove_worktree(worktree, dry_run=args.dry_run):
            removed_worktrees += 1
    if to_prune:
        removed_worktrees += _prune_worktrees(to_prune, dry_run=args.dry_run)

    current, default = scan.current_branch, scan.default_branch
    deleted_local = deleted_remote = archived = 0
    for branch, action in outcome.branches:
        if _protect(branch, current, default, config):
            continue
        if action in (Action.DELETE, Action.DELETE_LOCAL):
            if branch.has_local and _delete_local(branch, dry_run=args.dry_run):
                deleted_local += 1
            # DELETE_LOCAL leaves origin alone: the remote branch is the keeper
            if (
                action is Action.DELETE
                and branch.has_remote
                and _delete_remote(branch, dry_run=args.dry_run)
            ):
                deleted_remote += 1
        elif action is Action.ARCHIVE:
            if _archive(branch, dry_run=args.dry_run):
                archived += 1

    # STASHES LAST, IN DESCENDING INDEX ORDER, and the order is load-bearing
    # rather than cosmetic: a stash selector is a reflog position. Dropping
    # stash@{1} renumbers everything above it ({2}->{1}, {3}->{2}, ...) and
    # leaves everything below alone. Going highest-first means every selector we
    # have not touched yet sits below the one we are touching, so it never moves.
    # Ascending order would silently drop the WRONG stash. Do NOT split this into
    # "all drops, then the restore" either — that reorders across indices and
    # reintroduces the same bug.
    dropped_stashes = restored_stashes = 0
    restore_done = False
    for stash, stash_action in sorted(
        outcome.stashes, key=lambda pair: pair[0].index, reverse=True
    ):
        if stash_action is StashAction.KEEP:
            continue
        if stash_action in RESTORE_ACTIONS and restore_done:
            # the TUI caps this at one; re-asserted here so the invariant holds
            # for any caller, since scan_repo is a documented public entry point
            ui.warn(f"skipping {stash_action} of {stash.selector}: one restore per run")
            continue
        if not _stash_unchanged(stash):
            ui.warn(f"skipping {stash.selector}: it no longer points at {stash.sha[:8]}")
            continue
        if stash_action is StashAction.DROP:
            if _drop_stash(stash, dry_run=args.dry_run):
                dropped_stashes += 1
        else:
            restore_done = True
            if _restore_stash(stash, keep=stash_action is StashAction.APPLY, dry_run=args.dry_run):
                restored_stashes += 1

    prefix = "[cyan]\\[dry-run][/cyan] " if args.dry_run else ""
    summary = (
        f"\n{prefix}Done: deleted {deleted_local} local, "
        f"{deleted_remote} remote, archived {archived}"
    )
    # each clause is appended only when something happened, so repos without
    # worktrees or stashes produce byte-identical output
    if removed_worktrees:
        summary += f", removed {removed_worktrees} worktrees"
    if restored_stashes:
        summary += f", restored {restored_stashes} stashes"
    if dropped_stashes:
        summary += f", dropped {dropped_stashes} stashes"
    ui.info(f"{summary}.")
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
