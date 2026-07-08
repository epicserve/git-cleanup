"""All interactive output and prompts live here so tests can bypass them."""

from __future__ import annotations

import sys
from collections.abc import Sequence

import questionary
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
    parts = []
    if years:
        parts.append(f"{years}y")
    if months:
        parts.append(f"{months}m")
    if day_part or not parts:
        parts.append(f"{day_part}d")
    return " ".join(parts)


def _sync_label(b: BranchInfo) -> str:
    """Ahead/behind of the local branch vs its upstream."""
    if not b.has_local:
        return ""
    if b.upstream_gone:
        return "[red]gone[/red]"
    if b.ahead is None:
        return "[dim]—[/dim]"  # no upstream
    parts = []
    if b.ahead:
        parts.append(f"[yellow]↑{b.ahead}[/yellow]")
    if b.behind:
        parts.append(f"↓{b.behind}")
    return " ".join(parts) if parts else "[green]✓[/green]"


def _describe(b: BranchInfo) -> str:
    reasons = []
    if b.merged:
        reasons.append("merged")
    if b.issue_done and b.issue:
        reasons.append(f"{b.issue.key} {b.issue.status}")
    if not reasons and b.issue:
        reasons.append(f"{b.issue.key} {b.issue.status}")
    if b.has_unpushed:
        reasons.append(f"↑{b.ahead} unpushed")
    reasons.append(format_age(b.age_days))
    return f"{b.name}  ({', '.join(reasons)})"


def _interactive() -> bool:
    if sys.stdin.isatty():
        return True
    warn("stdin is not a terminal; skipping prompt (nothing selected)")
    return False


def select_branches(
    branches: Sequence[BranchInfo],
    message: str,
    preselect: bool,
) -> list[BranchInfo]:
    """Checkbox multi-select; every recommendation can be unselected."""
    if not _interactive():
        return []
    choices = [
        questionary.Choice(title=_describe(b), value=b, checked=preselect)
        for b in branches
    ]
    selected = questionary.checkbox(message, choices=choices).ask()
    return selected or []


def confirm(message: str, default: bool = False) -> bool:
    if not _interactive():
        return False
    answer = questionary.confirm(message, default=default).ask()
    return bool(answer)


def info(message: str) -> None:
    console.print(message)


def warn(message: str) -> None:
    console.print(f"[yellow]⚠ {message}[/yellow]")


def dry_run_note(action: str) -> None:
    console.print(rf"[cyan]\[dry-run][/cyan] would {action}")
