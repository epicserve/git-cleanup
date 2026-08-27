"""Console output helpers (rich). Interactive selection lives in tui.py."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from rich.console import Console
from rich.table import Table

from git_cleanup.models import BranchInfo, StashInfo, WorktreeInfo

console = Console()


def render_branch_table(branches: Sequence[BranchInfo]) -> None:
    table = Table(title="Branches", header_style="bold")
    # ratio splits the slack: with 10 columns at 80 cols something has to fold,
    # and the branch name is the row's identity, so let the author fold first
    table.add_column("Branch", overflow="fold", ratio=3)
    table.add_column("Local", justify="center")
    table.add_column("Remote", justify="center")
    table.add_column("WT", justify="center")
    table.add_column("Merged", justify="center")
    table.add_column("Sync", justify="center")
    table.add_column("Author", overflow="fold", ratio=1)
    table.add_column("Age", justify="right")
    table.add_column("Issue")
    table.add_column("Status")

    for b in branches:
        name = b.name
        if b.is_current:
            name = f"[bold green]{name}*[/bold green]"
        elif b.is_default or b.is_protected:
            name = f"[dim]{name}[/dim]"
        status = b.issue.status if b.issue else "—"
        status_style = "green" if b.issue_done else ""
        table.add_row(
            name,
            "[green]✓[/green]" if b.has_local else "",
            "[green]✓[/green]" if b.has_remote else "",
            "[green]✓[/green]" if b.has_worktree else "",
            "[green]✓[/green]" if b.merged else "",
            _sync_label(b),
            b.author_name,
            format_age(b.age_days),
            b.issue_key or "—",
            f"[{status_style}]{status}[/{status_style}]" if status_style else status,
        )
    console.print(table)


def format_worktree_path(path: Path) -> str:
    """Worktree path with $HOME collapsed to '~'."""
    home = Path.home()
    if path == home:
        return "~"
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def _worktree_branch_text(wt: WorktreeInfo) -> str:
    if wt.bare:
        return "(bare)"
    if wt.short_branch:
        return wt.short_branch
    if wt.head:
        return f"({wt.head[:8]}) detached"
    return "—"


def worktree_flags(wt: WorktreeInfo) -> list[str]:
    """Every notable state of a worktree, as words.

    Orthogonal and all shown: five states with no established icon vocabulary,
    so words beat invented glyphs.
    """
    flags = []
    if wt.is_main:
        flags.append("main")
    if wt.is_missing:
        flags.append("missing")
    if wt.locked:
        flags.append("locked")
    if wt.is_dirty:
        flags.append(f"dirty {wt.dirty_count}")
    if wt.detached:
        flags.append("detached")
    if wt.bare:
        flags.append("bare")
    return flags


_FLAG_MARKUP = {
    "main": "dim",
    "bare": "dim",
    "detached": "dim",
    "missing": "yellow",
    "locked": "yellow",
    "dirty": "bold red",
}


def _flags_label(wt: WorktreeInfo) -> str:
    """Flags styled for the rich overview table."""
    parts = []
    for flag in worktree_flags(wt):
        style = _FLAG_MARKUP.get(flag.split()[0], "")
        parts.append(f"[{style}]{flag}[/{style}]" if style else flag)
    return " ".join(parts)


def render_worktree_table(worktrees: Sequence[WorktreeInfo]) -> None:
    table = Table(title="Worktrees", header_style="bold")
    # path is the row's identity here (the TUI keeps it off the grid); fold it
    # so the branch decision columns still fit at 80
    table.add_column("Worktree", overflow="fold", ratio=3)
    table.add_column("Branch", overflow="fold")
    table.add_column("Local", justify="center")
    table.add_column("Remote", justify="center")
    table.add_column("Merged", justify="center")
    table.add_column("Sync", justify="center")
    table.add_column("Author", overflow="fold", ratio=1)
    table.add_column("Age", justify="right")
    table.add_column("Issue")
    table.add_column("Status")
    table.add_column("Flags")

    for wt in worktrees:
        path = format_worktree_path(wt.path)
        if wt.is_current:
            path = f"[bold green]{path}*[/bold green]"
        elif not wt.removable:
            path = f"[dim]{path}[/dim]"
        branch = _worktree_branch_text(wt)
        if not wt.removable:
            branch = f"[dim]{branch}[/dim]"
        info = wt.branch_info
        if info is None:
            local = remote = merged = sync = ""
            author = age = issue = status = "—"
        else:
            local = "[green]✓[/green]" if info.has_local else ""
            remote = "[green]✓[/green]" if info.has_remote else ""
            merged = "[green]✓[/green]" if info.merged else ""
            sync = _sync_label(info)
            author = info.author_name
            age = format_age(info.age_days)
            issue = info.issue_key or "—"
            status_text = info.issue.status if info.issue else "—"
            status_style = "green" if info.issue_done else ""
            status = (
                f"[{status_style}]{status_text}[/{status_style}]" if status_style else status_text
            )
        table.add_row(
            path,
            branch,
            local,
            remote,
            merged,
            sync,
            author,
            age,
            issue,
            status,
            _flags_label(wt),
        )
    console.print(table)


def stash_files_label(stash: StashInfo) -> str:
    """Changed-file count, with +u when the stash also carries untracked files."""
    if stash.file_count is None:
        return "—"
    return f"{stash.file_count} +u" if stash.has_untracked else str(stash.file_count)


def render_stash_table(stashes: Sequence[StashInfo]) -> None:
    table = Table(title="Stashes", header_style="bold")
    table.add_column("Stash")
    table.add_column("Message", overflow="fold", ratio=3)
    table.add_column("Branch", overflow="fold", ratio=1)
    table.add_column("Age", justify="right")
    table.add_column("Files", justify="right")

    for stash in stashes:
        table.add_row(
            stash.selector,
            f"[dim]{stash.message}[/dim]" if stash.wip else stash.message,
            stash.branch or "[dim](detached)[/dim]",
            format_age(stash.age_days),
            stash_files_label(stash),
        )
    console.print(table)


def format_age(days: int) -> str:
    """Format an age in days as years/months/days, e.g. '2y 6m 9d'."""
    years, rest = divmod(days, 365)
    months, day_part = divmod(rest, 30)
    if months == 12:  # 365/30 remainder quirk: never show "12m"
        years, months = years + 1, 0
    parts = []
    if years:
        parts.append(f"{years}y")
    if months:
        parts.append(f"{months}m")
    if day_part or not parts:
        parts.append(f"{day_part}d")
    return " ".join(parts)


def _sync_text(b: BranchInfo) -> str:
    """Ahead/behind of the local branch vs its upstream, as plain text."""
    if not b.has_local:
        return ""
    if b.upstream_gone:
        return "gone"
    if b.ahead is None:
        return "—"  # no upstream
    parts = []
    if b.ahead:
        parts.append(f"↑{b.ahead}")
    if b.behind:
        parts.append(f"↓{b.behind}")
    return " ".join(parts) if parts else "✓"


_SYNC_STYLES = {"gone": "red", "—": "dim", "✓": "green"}


def _sync_label(b: BranchInfo) -> str:
    """Sync state styled for the rich overview table."""
    text = _sync_text(b)
    if not text:
        return ""
    style = "yellow" if text.startswith("↑") else _SYNC_STYLES.get(text, "")
    return f"[{style}]{text}[/{style}]" if style else text


def info(message: str) -> None:
    console.print(message)


def warn(message: str) -> None:
    console.print(f"[yellow]⚠ {message}[/yellow]")


def dry_run_note(action: str) -> None:
    console.print(rf"[cyan]\[dry-run][/cyan] would {action}")
