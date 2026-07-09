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


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


# questionary renders checkbox rows as e.g. " ❯ ◉ <title>" — 5 columns of
# pointer/marker before the title — and separators with a 3-space indent, so
# the header separator needs 2 extra columns to line up with choice titles
_CHOICE_INDENT = 5
_HEADER_PAD = " " * 2


def _choice_rows(branches: Sequence[BranchInfo]) -> tuple[str, list[str]]:
    """Format branches as aligned columns for checkbox choices.

    Returns (header, rows); rows[i] corresponds to branches[i].
    """
    headers = ("BRANCH", "SYNC", "AUTHOR", "AGE", "MRG", "ISSUE", "STATUS")
    fixed_caps = (40, 8, 16, 12, 3, 12, 16)
    cells = [
        (
            b.name,
            _sync_text(b),
            b.author_name,
            format_age(b.age_days),
            "✓" if b.merged else "",
            b.issue_key or "—",
            (b.issue.status if b.issue else "—"),
        )
        for b in branches
    ]

    widths = [
        min(cap, max(len(header), *(len(row[col]) for row in cells)))
        for col, (header, cap) in enumerate(zip(headers, fixed_caps, strict=True))
    ]
    # shrink the branch column if the terminal is narrow (2-space gutters)
    other = sum(widths[1:]) + 2 * len(widths) + _CHOICE_INDENT
    widths[0] = max(20, min(widths[0], console.width - other))

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(
            _truncate(value, width).ljust(width)
            for value, width in zip(row, widths, strict=True)
        ).rstrip()

    return fmt(headers), [fmt(row) for row in cells]


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
    header, rows = _choice_rows(branches)
    choices: list[questionary.Separator | questionary.Choice] = [
        questionary.Separator(_HEADER_PAD + header)
    ]
    choices += [
        questionary.Choice(title=row, value=b, checked=preselect)
        for row, b in zip(rows, branches, strict=True)
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
