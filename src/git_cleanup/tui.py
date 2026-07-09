"""Full-screen interactive branch cleanup (Textual app).

The app never mutates the repository: it returns the user's confirmed
decisions to the caller, which executes them after the TUI exits.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, Static

from git_cleanup import planner
from git_cleanup.models import Action, BranchInfo
from git_cleanup.ui import _sync_text, format_age

type Decision = tuple[BranchInfo, Action]


class SpecInput(Input):
    """Filter/sort spec input; Esc closes it without applying."""

    BINDINGS = [Binding("escape", "cancel_input", "Cancel", show=False)]

    def action_cancel_input(self) -> None:
        app = self.app
        assert isinstance(app, CleanupApp)
        app.close_spec_input()

_ACTION_STYLES = {
    Action.KEEP: "dim",
    Action.DELETE: "bold red",
    Action.ARCHIVE: "yellow",
}
_CYCLE = {Action.KEEP: Action.DELETE, Action.DELETE: Action.ARCHIVE, Action.ARCHIVE: Action.KEEP}


def run_tui(
    branches: Sequence[BranchInfo],
    *,
    my_email: str,
    include_all: bool,
    archive_age_days: int,
    sort_fields: list[tuple[str, bool]],
    filter_spec: str = "",
    dry_run: bool = False,
) -> list[Decision] | None:
    """Run the cleanup TUI. Returns confirmed decisions, or None if quit."""
    app = CleanupApp(
        branches,
        my_email=my_email,
        include_all=include_all,
        archive_age_days=archive_age_days,
        sort_fields=sort_fields,
        filter_spec=filter_spec,
        dry_run=dry_run,
    )
    return app.run()


class ReviewScreen(ModalScreen[bool]):
    """Grouped summary of pending actions with a final confirm."""

    BINDINGS = [
        Binding("y", "confirm", "Confirm"),
        Binding("n,escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    ReviewScreen { align: center middle; }
    #review-box {
        width: 80%; max-width: 100; max-height: 80%;
        border: round $primary; padding: 1 2; background: $surface;
    }
    #review-body { max-height: 100%; overflow-y: auto; }
    #review-buttons { height: auto; align-horizontal: center; margin-top: 1; }
    #review-buttons Button { margin: 0 2; }
    .remote-warning { border: round red; padding: 0 1; margin-top: 1; }
    """

    def __init__(self, decisions: list[Decision], dry_run: bool) -> None:
        super().__init__()
        self._decisions = decisions
        self._dry_run = dry_run

    def compose(self) -> ComposeResult:
        deletes = [b for b, a in self._decisions if a is Action.DELETE]
        archives = [b for b, a in self._decisions if a is Action.ARCHIVE]
        local = [b for b in deletes if b.has_local]
        remote = [b for b in deletes if b.has_remote]

        lines: list[Text] = []
        title = "Review actions" + (" (DRY RUN — nothing will change)" if self._dry_run else "")
        with Vertical(id="review-box"):
            yield Label(Text(title, style="bold"))
            with Vertical(id="review-body"):
                if local:
                    lines.append(Text(f"Delete {len(local)} local:", style="bold"))
                    for b in local:
                        line = Text(f"  {b.name}")
                        if b.has_unpushed:
                            line.append(f"  ↑{b.ahead} unpushed — will be lost", style="bold red")
                        lines.append(line)
                if archives:
                    lines.append(Text(f"\nArchive {len(archives)}:", style="bold"))
                    lines.extend(
                        Text(f"  {b.name}  → tag archive/{b.name}, then delete")
                        for b in archives
                    )
                for line in lines:
                    yield Static(line)
                if remote:
                    warning = Text(f"Delete {len(remote)} on origin:\n", style="bold red")
                    for b in remote:
                        warning.append(f"  origin/{b.name}\n")
                    warning.append("These branches will be deleted for everyone.", style="red")
                    yield Static(warning, classes="remote-warning")
            with Horizontal(id="review-buttons"):
                yield Button(
                    "Confirm (y)",
                    id="confirm",
                    variant="error" if remote else "primary",
                )
                yield Button("Cancel (n)", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class CleanupApp(App[list[Decision] | None]):
    """One table of all branches; mark each row keep/delete/archive."""

    TITLE = "git-cleanup"

    BINDINGS = [
        Binding("space", "cycle", "Cycle action"),
        Binding("d", "mark('delete')", "Delete"),
        Binding("a", "mark('archive')", "Archive"),
        Binding("k", "mark('keep')", "Keep"),
        Binding("slash", "open_filter", "Filter"),
        Binding("s", "open_sort", "Sort"),
        Binding("q,escape", "quit_nochange", "Quit"),
    ]

    DEFAULT_CSS = """
    #status { height: 1; padding: 0 1; background: $primary-darken-2; }
    #spec-input { display: none; dock: bottom; }
    DataTable { height: 1fr; }
    """

    def __init__(
        self,
        branches: Sequence[BranchInfo],
        *,
        my_email: str,
        include_all: bool,
        archive_age_days: int,
        sort_fields: list[tuple[str, bool]],
        filter_spec: str = "",
        dry_run: bool = False,
    ) -> None:
        super().__init__()
        self._all = list(branches)
        self._my_email = my_email
        self._archive_age_days = archive_age_days
        self._sort_fields = sort_fields
        self._filter_spec = filter_spec
        self._dry_run = dry_run
        self._by_name = {b.name: b for b in self._all}
        self._input_mode = ""  # "filter" | "sort" while the spec input is open

        recommended = planner.recommend_actions(
            self._all,
            for_email=my_email,
            include_all=include_all,
            archive_age_days=archive_age_days,
        )
        # pre-mark deletions only; archiving stays opt-in (shown as a hint)
        self.actions: dict[str, Action] = {
            b.name: (
                Action.DELETE if recommended.get(b.name) is Action.DELETE else Action.KEEP
            )
            for b in self._all
            if not (b.is_current or b.is_default or b.is_protected)
        }
        self._visible = self._apply_view(self._all)

    # ---------- view helpers ----------

    def _apply_view(self, branches: Sequence[BranchInfo]) -> list[BranchInfo]:
        result = list(branches)
        if self._filter_spec:
            result = planner.filter_branches(
                result, planner.parse_filter(self._filter_spec), self._my_email
            )
        return planner.sort_branches(result, self._sort_fields)

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        table = DataTable(cursor_type="row", zebra_stripes=True)
        yield table
        yield SpecInput(id="spec-input")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for key, label in (
            ("action", "Action"),
            ("branch", "Branch"),
            ("local", "Local"),
            ("remote", "Remote"),
            ("sync", "Sync"),
            ("author", "Author"),
            ("age", "Age"),
            ("merged", "Merged"),
            ("issue", "Issue"),
            ("status", "Status"),
        ):
            table.add_column(label, key=key)
        self._rebuild_table()
        table.focus()

    def _action_cell(self, name: str) -> Text:
        action = self.actions.get(name, Action.KEEP)
        return Text(action.value, style=_ACTION_STYLES[action])

    def _row_cells(self, b: BranchInfo) -> list[Text]:
        name = Text(b.name + ("*" if b.is_current else ""))
        if b.is_current or b.is_default or b.is_protected:
            name.stylize("dim")
        age_style = "yellow" if b.age_days >= self._archive_age_days else ""
        return [
            self._action_cell(b.name),
            name,
            Text("●" if b.has_local else ""),
            Text("●" if b.has_remote else ""),
            Text(_sync_text(b)),
            Text(b.author_name),
            Text(format_age(b.age_days), style=age_style),
            Text("✓" if b.merged else "", style="green"),
            Text(b.issue_key or "—"),
            Text(
                b.issue.status if b.issue else "—",
                style="green" if b.issue_done else "",
            ),
        ]

    def _rebuild_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for b in self._visible:
            table.add_row(*self._row_cells(b), key=b.name)
        self._refresh_status()

    def _refresh_status(self) -> None:
        deletes = sum(1 for a in self.actions.values() if a is Action.DELETE)
        archives = sum(1 for a in self.actions.values() if a is Action.ARCHIVE)
        parts = [
            f"{len(self._visible)} of {len(self._all)} branches"
            if len(self._visible) != len(self._all)
            else f"{len(self._all)} branches",
            f"{deletes} delete",
            f"{archives} archive",
        ]
        visible_names = {b.name for b in self._visible}
        hidden_marked = sum(
            1
            for name, action in self.actions.items()
            if action is not Action.KEEP and name not in visible_names
        )
        if hidden_marked:
            parts.append(f"{hidden_marked} marked hidden by filter")
        if self._filter_spec:
            parts.append(f"filter: {self._filter_spec}")
        if self._dry_run:
            parts.append("DRY RUN")
        self.query_one("#status", Static).update(Text(" · ".join(parts), style="bold"))

    # ---------- actions ----------

    def _cursor_branch(self) -> BranchInfo | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return self._by_name.get(row_key.value or "")

    def _set_action(self, branch: BranchInfo, action: Action) -> None:
        if branch.name not in self.actions:
            self.notify(f"{branch.name} is protected", severity="warning", timeout=3)
            return
        self.actions[branch.name] = action
        self.query_one(DataTable).update_cell(
            branch.name, "action", self._action_cell(branch.name), update_width=False
        )
        self._refresh_status()

    def action_cycle(self) -> None:
        branch = self._cursor_branch()
        if branch:
            current = self.actions.get(branch.name, Action.KEEP)
            self._set_action(branch, _CYCLE[current])

    def action_mark(self, action: str) -> None:
        branch = self._cursor_branch()
        if branch:
            self._set_action(branch, Action(action))

    def _decisions(self) -> list[Decision]:
        return [
            (self._by_name[name], action)
            for name, action in self.actions.items()
            if action is not Action.KEEP
        ]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._open_review()

    def _open_review(self) -> None:
        decisions = self._decisions()
        if not decisions:
            self.notify("Nothing marked — use space/d/a to mark branches", timeout=3)
            return

        def handle(confirmed: bool | None) -> None:
            if confirmed:
                self.exit(decisions)

        self.push_screen(ReviewScreen(decisions, self._dry_run), handle)

    def action_quit_nochange(self) -> None:
        self.exit(None)

    # ---------- filter / sort inputs ----------

    def _open_input(self, mode: str, value: str, placeholder: str) -> None:
        self._input_mode = mode
        spec_input = self.query_one("#spec-input", SpecInput)
        spec_input.value = value
        spec_input.placeholder = placeholder
        spec_input.display = True
        spec_input.focus()

    def action_open_filter(self) -> None:
        self._open_input(
            "filter", self._filter_spec, "filter: e.g. mine,age>6m,status!=done (empty clears)"
        )

    def action_open_sort(self) -> None:
        spec = ",".join(("-" if desc else "") + name for name, desc in self._sort_fields)
        self._open_input("sort", spec, "sort: e.g. -age,author")

    def close_spec_input(self) -> None:
        spec_input = self.query_one("#spec-input", SpecInput)
        spec_input.display = False
        self._input_mode = ""
        self.query_one(DataTable).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        mode, value = self._input_mode, event.value.strip()
        try:
            if mode == "filter":
                planner.parse_filter(value)  # validate before adopting
                self._filter_spec = value
            elif mode == "sort":
                self._sort_fields = planner.parse_sort(value or "branch")
        except ValueError as exc:
            self.notify(str(exc), severity="error", timeout=5)
            return
        self._visible = self._apply_view(self._all)
        self.close_spec_input()
        self._rebuild_table()
