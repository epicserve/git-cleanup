"""Console output helpers (rich). Interactive selection lives in tui.py."""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.table import Table

from git_cleanup.models import BranchInfo

console = Console()


def render_branch_table(branches: Sequence[BranchInfo]) -> None:
    table = Table(title="Branches", header_style="bold")
    table.add_column("Branch", overflow="fold")
    table.add_column("Local", justify="center")
    table.add_column("Remote", justify="center")
    table.add_column("Sync", justify="center")
    table.add_column("Author")
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
            _sync_label(b),
            b.author_name,
            format_age(b.age_days),
            "[green]✓[/green]" if b.merged else "",
            b.issue_key or "—",
            f"[{status_style}]{status}[/{status_style}]" if status_style else status,
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
