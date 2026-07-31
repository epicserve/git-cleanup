"""Console output helpers (rich). Interactive selection lives in tui.py."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from rich.console import Console
from rich.table import Table

from git_cleanup.models import BranchInfo, WorktreeInfo

console = Console()


def render_branch_table(branches: Sequence[BranchInfo]) -> None:
    table = Table(title="Branches", header_style="bold")
    # ratio splits the slack: with 10 columns at 80 cols something has to fold,
    # and the branch name is the row's identity, so let the author fold first
    table.add_column("Branch", overflow="fold", ratio=3)
    table.add_column("Local", justify="center")
    table.add_column("Remote", justify="center")
    table.add_column("WT", justify="center")
    table.add_column("Sync", justify="center")
    table.add_column("Author", overflow="fold", ratio=1)
    table.add_column("Age", justify="right")
    table.add_column("Merged", justify="center")
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
            "●" if b.has_local else "",
            "●" if b.has_remote else "",
            "●" if b.has_worktree else "",
            _sync_label(b),
            b.author_name,
            format_age(b.age_days),
            "[green]✓[/green]" if b.merged else "",
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


def _worktree_state_text(wt: WorktreeInfo) -> str:
    """The single most important thing about a worktree, as plain text.

    Precedence: a missing directory outranks a lock (the lock is moot once the
    checkout is gone), which outranks the structural bare/main/detached facts.
    """
    if wt.is_missing:
        return "missing"
    if wt.locked:
        return "locked"
    if wt.bare:
        return "bare"
    if wt.is_main:
        return "main"
    if wt.detached:
        return "detached"
    return ""


def worktree_flags(wt: WorktreeInfo) -> list[str]:
    """Every notable state of a worktree, as words.

    Unlike _worktree_state_text these are orthogonal and all shown: five states
    with no established icon vocabulary, so words beat invented glyphs.
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


def render_worktree_table(worktrees: Sequence[WorktreeInfo]) -> None:
    table = Table(title="Worktrees", header_style="bold")
    table.add_column("Worktree", overflow="fold")
    table.add_column("Branch", overflow="fold")
    table.add_column("Age", justify="right")
    table.add_column("Merged", justify="center")
    table.add_column("Issue")
    table.add_column("Changes", justify="right")
    table.add_column("State")

    for wt in worktrees:
        path = format_worktree_path(wt.path)
        if wt.is_current:
            path = f"[bold green]{path}*[/bold green]"
        elif not wt.removable:
            path = f"[dim]{path}[/dim]"
        if wt.dirty_count is None:
            changes = "—"
        else:
            changes = f"[red]{wt.dirty_count}[/red]" if wt.dirty_count else "0"
        state = _worktree_state_text(wt)
        if state in ("missing", "locked"):
            state = f"[yellow]{state}[/yellow]"
        table.add_row(
            path,
            wt.short_branch or ("(bare)" if wt.bare else "(detached)"),
            format_age(wt.age_days) if wt.age_days is not None else "—",
            "[green]✓[/green]" if wt.merged else "",
            (wt.branch_info.issue_key if wt.branch_info else None) or "—",
            changes,
            state,
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
