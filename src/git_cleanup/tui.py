"""Full-screen interactive branch and worktree cleanup (Textual app).

The app never mutates the repository: it returns the user's confirmed
decisions to the caller, which executes them after the TUI exits.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
    Tabs,
)

from git_cleanup import planner
from git_cleanup.models import Action, BranchInfo, Outcome, WorktreeAction, WorktreeInfo
from git_cleanup.ui import _sync_text, format_age, format_worktree_path, worktree_flags

type Decision = tuple[BranchInfo, Action]

TAB_BRANCHES = "tab-branches"
TAB_WORKTREES = "tab-worktrees"
_TABLE_IDS = {TAB_BRANCHES: "#branch-table", TAB_WORKTREES: "#worktree-table"}


class SpecInput(Input):
    """Filter/sort spec input; Esc closes it without applying."""

    BINDINGS = [Binding("escape", "cancel_input", "Cancel", show=False)]

    def action_cancel_input(self) -> None:
        app = self.app
        assert isinstance(app, CleanupApp)
        app.close_spec_input()


# Row-scoped bindings live on the tables, not the app, so the Footer (which
# reads the focused widget's binding chain) advertises only the active tab's
# keys. The "app." prefix dispatches to CleanupApp's action methods.
class BranchTable(DataTable):
    BINDINGS = [
        Binding("enter", "select_cursor", "Review"),
        Binding("space", "app.cycle", "Cycle action"),
        Binding("d", "app.mark_delete", "Delete (again: local)"),
        Binding("a", "app.mark('archive')", "Archive"),
        Binding("k", "app.mark('keep')", "Keep"),
        Binding("o", "app.open_compare", "Compare"),
        Binding("slash", "app.open_filter", "Filter"),
        Binding("s", "app.open_sort", "Sort"),
        Binding("r", "app.reset_view", "Reset view"),
    ]


class WorktreeTable(DataTable):
    # no filter/sort keys: worktree lists are short. 'r' is deliberately not
    # reused for "remove" — it means "Reset view" one tab over, and a key that
    # means reset in one place and destruction in another is bad muscle memory.
    BINDINGS = [
        Binding("enter", "select_cursor", "Review"),
        Binding("space", "app.cycle_worktree", "Toggle remove"),
        Binding("d", "app.mark_worktree('remove')", "Remove"),
        Binding("k", "app.mark_worktree('keep')", "Keep"),
    ]


_ACTION_STYLES = {
    Action.KEEP: "dim",
    Action.DELETE: "bold red",
    Action.DELETE_LOCAL: "red",  # single-sided: less alarming than deleting for everyone
    Action.ARCHIVE: "yellow",
}
_WORKTREE_ACTION_STYLES = {
    WorktreeAction.KEEP: "dim",
    WorktreeAction.REMOVE: "bold red",
}
_FLAG_STYLES = {
    "main": "dim",
    "bare": "dim",
    "detached": "dim",
    "missing": "yellow",
    "locked": "yellow",
    "dirty": "bold red",
}
_CYCLE = {
    Action.KEEP: Action.DELETE,
    Action.DELETE: Action.DELETE_LOCAL,
    Action.DELETE_LOCAL: Action.ARCHIVE,
    Action.ARCHIVE: Action.KEEP,
}


def _next_action(branch: BranchInfo, current: Action) -> Action:
    """Next action in the cycle, skipping delete-local when it adds nothing."""
    following = _CYCLE[current]
    if following is Action.DELETE_LOCAL and not branch.has_both_refs:
        return _CYCLE[following]
    return following


def _flags_cell(wt: WorktreeInfo) -> Text:
    cell = Text()
    for flag in worktree_flags(wt):
        if cell.plain:
            cell.append(" ")
        cell.append(flag, style=_FLAG_STYLES.get(flag.split()[0], ""))
    return cell


def _worktree_branch_text(wt: WorktreeInfo) -> str:
    if wt.bare:
        return "(bare)"
    if wt.short_branch:
        return wt.short_branch
    if wt.head:
        return f"({wt.head[:8]}) detached"
    return "—"


def run_tui(
    branches: Sequence[BranchInfo],
    *,
    my_email: str,
    include_all: bool,
    archive_age_days: int,
    sort_fields: list[tuple[str, bool]],
    filter_spec: str = "",
    dry_run: bool = False,
    on_view_change: Callable[[str, str], None] | None = None,
    compare_url: Callable[[str], str] | None = None,
    worktrees: Sequence[WorktreeInfo] = (),
) -> Outcome | None:
    """Run the cleanup TUI. Returns confirmed decisions, or None if quit.

    compare_url maps a branch name to the origin compare page for it;
    None when origin has no web URL (the 'o' key then explains itself).
    """
    app = CleanupApp(
        branches,
        my_email=my_email,
        include_all=include_all,
        archive_age_days=archive_age_days,
        sort_fields=sort_fields,
        filter_spec=filter_spec,
        dry_run=dry_run,
        on_view_change=on_view_change,
        compare_url=compare_url,
        worktrees=worktrees,
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
    .worktree-warning { border: round red; padding: 0 1; margin-top: 1; }
    """

    def __init__(self, outcome: Outcome, dry_run: bool) -> None:
        super().__init__()
        self._outcome = outcome
        self._dry_run = dry_run
        self._removing_paths = {
            wt.name for wt, action in outcome.worktrees if action is WorktreeAction.REMOVE
        }

    def _checkout_note(self, branch: BranchInfo) -> Text | None:
        """Warn when git will refuse a branch delete because it is checked out.

        Applies to archives too: _archive force-deletes the local branch, and
        git refuses that identically.
        """
        if not branch.has_worktree:
            return None
        assert branch.worktree_path is not None
        label = format_worktree_path(branch.worktree_path)
        if str(branch.worktree_path) in self._removing_paths:
            return Text(f"  → after removing worktree {label}", style="green")
        return Text(f"  ✗ checked out in {label} — delete will fail", style="red")

    def compose(self) -> ComposeResult:
        removals = [wt for wt, a in self._outcome.worktrees if a is WorktreeAction.REMOVE]
        prunable = [wt for wt in removals if wt.prunable]
        dirty = [wt for wt in removals if not wt.prunable and wt.needs_force]
        clean = [wt for wt in removals if not wt.prunable and not wt.needs_force]

        decisions = self._outcome.branches
        archives = [b for b, a in decisions if a is Action.ARCHIVE]
        local = [
            b for b, a in decisions if b.has_local and a in (Action.DELETE, Action.DELETE_LOCAL)
        ]
        remote = [b for b, a in decisions if b.has_remote and a is Action.DELETE]
        kept_on_origin = {
            b.name for b, a in decisions if a is Action.DELETE_LOCAL and b.has_remote
        }

        lines: list[Text] = []
        title = "Review actions" + (" (DRY RUN — nothing will change)" if self._dry_run else "")
        with Vertical(id="review-box"):
            yield Label(Text(title, style="bold"))
            with Vertical(id="review-body"):
                # worktrees first: that is the execution order, because
                # `git branch -d` refuses a branch checked out anywhere
                if clean:
                    lines.append(Text(f"Remove {len(clean)} worktrees:", style="bold"))
                    for wt in clean:
                        line = Text(f"  {format_worktree_path(wt.path)}")
                        if wt.short_branch:
                            line.append(f"  → branch {wt.short_branch} stays", style="green")
                        lines.append(line)
                if prunable:
                    lines.append(Text(f"\nClear {len(prunable)} broken entries:", style="bold"))
                    for wt in prunable:
                        line = Text(f"  {format_worktree_path(wt.path)}")
                        line.append("  → prune (directory is gone)", style="yellow")
                        lines.append(line)
                if local:
                    lines.append(Text(f"\nDelete {len(local)} local:", style="bold"))
                    for b in local:
                        line = Text(f"  {b.name}")
                        if b.name in kept_on_origin:
                            line.append(f"  → keeping origin/{b.name}", style="green")
                        if b.has_unpushed:
                            line.append(f"  ↑{b.ahead} unpushed — will be lost", style="bold red")
                        lines.append(line)
                        note = self._checkout_note(b)
                        if note is not None:
                            lines.append(note)
                if archives:
                    lines.append(Text(f"\nArchive {len(archives)}:", style="bold"))
                    for b in archives:
                        lines.append(Text(f"  {b.name}  → tag archive/{b.name}, then delete"))
                        note = self._checkout_note(b)
                        if note is not None:
                            lines.append(note)
                for line in lines:
                    yield Static(line)
                if dirty:
                    warning = Text(
                        f"Remove {len(dirty)} worktrees with uncommitted changes:\n",
                        style="bold red",
                    )
                    for wt in dirty:
                        warning.append(
                            f"  {format_worktree_path(wt.path)}  ({wt.dirty_count} changed)\n"
                        )
                    warning.append(
                        "These will be removed with --force; the changes are not recoverable.",
                        style="red",
                    )
                    yield Static(warning, classes="worktree-warning")
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
                    variant="error" if (remote or dirty) else "primary",
                )
                yield Button("Cancel (n)", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class CleanupApp(App[Outcome | None]):
    """Two tabs: all branches (keep/delete/delete-local/archive) and all
    worktrees (keep/remove)."""

    TITLE = "git-cleanup"
    # ctrl+p is swallowed by VS Code's terminal (Quick Open); ctrl+k is the
    # conventional palette shortcut elsewhere (Slack, browsers, Linear)
    COMMAND_PALETTE_BINDING = "ctrl+k"

    # only tab switching and quit live at app level; everything row-scoped is on
    # the tables. 'b'/'w' are absolute rather than a toggle so the footer
    # descriptions stay honest and check_action can grey out the active tab.
    BINDINGS = [
        Binding("b", f"show_tab('{TAB_BRANCHES}')", "Branches"),
        Binding("w", f"show_tab('{TAB_WORKTREES}')", "Worktrees"),
        Binding("q,escape", "quit_nochange", "Quit"),
    ]

    # TabbedContent/TabPane are used un-subclassed, so a bare type selector
    # loses the specificity tie-break to their own DEFAULT_CSS (height: auto)
    # and the app would grow past the viewport. These must stay id-scoped.
    DEFAULT_CSS = """
    #status { height: 1; padding: 0 1; background: $primary-darken-2; }
    #status.dry-run { background: $warning-darken-2; color: auto; }
    #spec-input { display: none; dock: bottom; }
    #tabs { height: 1fr; }
    #tabs TabPane { height: 1fr; }
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
        on_view_change: Callable[[str, str], None] | None = None,
        compare_url: Callable[[str], str] | None = None,
        worktrees: Sequence[WorktreeInfo] = (),
    ) -> None:
        super().__init__()
        self._all = list(branches)
        self._my_email = my_email
        self._archive_age_days = archive_age_days
        self._sort_fields = sort_fields
        self._filter_spec = filter_spec
        self._dry_run = dry_run
        self._on_view_change = on_view_change
        self._compare_url = compare_url
        self._by_name = {b.name: b for b in self._all}
        self._input_mode = ""  # "filter" | "sort" while the spec input is open
        self._active_tab = TAB_BRANCHES

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

        self._worktrees = self._sort_worktrees(worktrees)
        # every worktree, so unmarkable rows still resolve and can explain
        # themselves; worktree_actions holds only the ones git could remove
        self._by_path = {wt.name: wt for wt in self._worktrees}
        recommended_worktrees = planner.recommend_worktree_actions(
            self._worktrees, for_email=my_email, include_all=include_all
        )
        self.worktree_actions: dict[str, WorktreeAction] = {
            wt.name: recommended_worktrees.get(wt.name, WorktreeAction.KEEP)
            for wt in self._worktrees
            if wt.removable
        }
        self._visible = self._apply_view(self._all)

    # ---------- view helpers ----------

    @staticmethod
    def _sort_worktrees(worktrees: Sequence[WorktreeInfo]) -> list[WorktreeInfo]:
        """Main worktree first, then by branch name, detached ones last."""
        return sorted(
            worktrees,
            key=lambda wt: (
                not wt.is_main,
                wt.short_branch is None,
                (wt.short_branch or "").lower(),
                str(wt.path),
            ),
        )

    def _apply_view(self, branches: Sequence[BranchInfo]) -> list[BranchInfo]:
        result = list(branches)
        if self._filter_spec:
            result = planner.filter_branches(
                result, planner.parse_filter(self._filter_spec), self._my_email
            )
        return planner.sort_branches(result, self._sort_fields)

    @property
    def _branch_table(self) -> DataTable:
        return self.query_one("#branch-table", DataTable)

    @property
    def _worktree_table(self) -> DataTable:
        return self.query_one("#worktree-table", DataTable)

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        # both panes are always composed: the main worktree is always a row, so
        # the tab is never empty and there is one layout to maintain
        with TabbedContent(id="tabs", initial=TAB_BRANCHES):
            with TabPane("Branches", id=TAB_BRANCHES):
                yield BranchTable(id="branch-table", cursor_type="row", zebra_stripes=True)
            with TabPane("Worktrees", id=TAB_WORKTREES):
                yield WorktreeTable(id="worktree-table", cursor_type="row", zebra_stripes=True)
        yield SpecInput(id="spec-input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#status", Static).set_class(self._dry_run, "dry-run")
        # Screen claims tab/shift+tab for focus_next, and focus landing on the
        # tab bar would silently kill every row binding; we cannot rebind those
        # keys, so make the bar unfocusable instead
        self.query_one(TabbedContent).query_one(Tabs).can_focus = False

        table = self._branch_table
        # the action column is sized for the longest label up front: cell updates
        # pass update_width=False, so a narrow column would truncate "delete-local"
        # down to a misleading "delete"
        table.add_column("Action", key="action", width=len(Action.DELETE_LOCAL))
        for key, label in (
            ("branch", "Branch"),
            ("local", "Local"),
            ("remote", "Remote"),
            ("worktree", "WT"),
            ("sync", "Sync"),
            ("author", "Author"),
            ("age", "Age"),
            ("merged", "Merged"),
            ("issue", "Issue"),
            ("status", "Status"),
        ):
            table.add_column(label, key=key)

        worktree_table = self._worktree_table
        worktree_table.add_column("Action", key="action", width=len(WorktreeAction.REMOVE))
        for key, label in (
            ("worktree", "Worktree"),
            ("branch", "Branch"),
            ("age", "Age"),
            ("merged", "Merged"),
            ("issue", "Issue"),
            ("status", "Status"),
            ("flags", "Flags"),
        ):
            worktree_table.add_column(label, key=key)

        self._rebuild_table()
        self._rebuild_worktree_table()
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
            Text("●" if b.has_worktree else ""),
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
        table = self._branch_table
        table.clear()
        for b in self._visible:
            table.add_row(*self._row_cells(b), key=b.name)
        self._refresh_status()

    def _worktree_action_cell(self, name: str) -> Text:
        action = self.worktree_actions.get(name, WorktreeAction.KEEP)
        return Text(action.value, style=_WORKTREE_ACTION_STYLES[action])

    def _worktree_row_cells(self, wt: WorktreeInfo) -> list[Text]:
        path = Text(format_worktree_path(wt.path) + ("*" if wt.is_current else ""))
        if not wt.removable:
            path.stylize("dim")
        age = wt.age_days
        age_style = "yellow" if age is not None and age >= self._archive_age_days else ""
        branch_info = wt.branch_info
        return [
            self._worktree_action_cell(wt.name),
            path,
            Text(_worktree_branch_text(wt)),
            Text(format_age(age) if age is not None else "—", style=age_style),
            Text("✓" if wt.merged else "", style="green"),
            Text((branch_info.issue_key if branch_info else None) or "—"),
            Text(
                branch_info.issue.status if branch_info and branch_info.issue else "—",
                style="green" if wt.issue_done else "",
            ),
            _flags_cell(wt),
        ]

    def _rebuild_worktree_table(self) -> None:
        table = self._worktree_table
        table.clear()
        for wt in self._worktrees:
            table.add_row(*self._worktree_row_cells(wt), key=wt.name)
        self._refresh_status()

    def _branch_status_parts(self) -> list[str]:
        deletes = sum(1 for a in self.actions.values() if a is Action.DELETE)
        local_only = sum(1 for a in self.actions.values() if a is Action.DELETE_LOCAL)
        archives = sum(1 for a in self.actions.values() if a is Action.ARCHIVE)
        parts = [
            f"{len(self._visible)} of {len(self._all)} branches"
            if len(self._visible) != len(self._all)
            else f"{len(self._all)} branches",
            f"{deletes} delete",
        ]
        if local_only:
            parts.append(f"{local_only} delete-local")
        parts.append(f"{archives} archive")
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
        return parts

    def _worktree_status_parts(self) -> list[str]:
        marked = [
            self._by_path[name]
            for name, action in self.worktree_actions.items()
            if action is not WorktreeAction.KEEP
        ]
        prune = sum(1 for wt in marked if wt.prunable)
        dirty = sum(1 for wt in marked if not wt.prunable and wt.needs_force)
        parts = [
            f"{len(self._worktrees)} worktrees",
            f"{len(marked) - prune} remove",
        ]
        if prune:
            parts.append(f"{prune} prune")
        if dirty:
            parts.append(f"{dirty} dirty — will force")
        return parts

    def _refresh_status(self) -> None:
        parts = []
        if self._dry_run:
            parts.append("DRY RUN — nothing will change")
        # a cross-tab tail so marks on the hidden tab are never invisible
        if self._active_tab == TAB_WORKTREES:
            parts += self._worktree_status_parts()
            elsewhere = sum(1 for a in self.actions.values() if a is not Action.KEEP)
            if elsewhere:
                parts.append(f"{elsewhere} branches marked")
        else:
            parts += self._branch_status_parts()
            elsewhere = sum(
                1 for a in self.worktree_actions.values() if a is not WorktreeAction.KEEP
            )
            if elsewhere:
                parts.append(f"{elsewhere} worktrees marked")
        self.query_one("#status", Static).update(Text(" · ".join(parts), style="bold"))

    # ---------- tabs ----------

    def action_show_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # every namespaced binding resolves to the app, so this hook now fires
        # for all of them: default to True and only special-case tab switching
        if action == "show_tab" and parameters:
            return None if parameters[0] == self._active_tab else True
        return True

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Switching panes blurs the old table, which would leave the app with no
        focus and therefore no row bindings — so refocus here. Also covers mouse
        clicks on the tab bar."""
        self._active_tab = event.pane.id or TAB_BRANCHES
        self.query_one(_TABLE_IDS[self._active_tab], DataTable).focus()
        self._refresh_status()
        self.refresh_bindings()

    # ---------- branch actions ----------

    @staticmethod
    def _cursor_key(table: DataTable) -> str | None:
        if not table.row_count:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return row_key.value

    def _cursor_branch(self) -> BranchInfo | None:
        key = self._cursor_key(self._branch_table)
        return self._by_name.get(key or "")

    def _set_action(self, branch: BranchInfo, action: Action) -> None:
        if branch.name not in self.actions:
            self.notify(f"{branch.name} is protected", severity="warning", timeout=3)
            return
        self.actions[branch.name] = action
        self._branch_table.update_cell(
            branch.name, "action", self._action_cell(branch.name), update_width=False
        )
        self._refresh_status()

    def action_cycle(self) -> None:
        branch = self._cursor_branch()
        if branch:
            current = self.actions.get(branch.name, Action.KEEP)
            self._set_action(branch, _next_action(branch, current))

    def action_mark(self, action: str) -> None:
        branch = self._cursor_branch()
        if branch:
            self._set_action(branch, Action(action))

    def action_mark_delete(self) -> None:
        """First press deletes both sides; pressing again toggles to local-only."""
        branch = self._cursor_branch()
        if branch is None:
            return
        if self.actions.get(branch.name) is not Action.DELETE:
            self._set_action(branch, Action.DELETE)
            return
        if not branch.has_both_refs:
            hint = (
                f"{branch.name} is not on origin — delete is already local-only"
                if not branch.has_remote
                else f"{branch.name} has no local branch — delete only affects origin"
            )
            self.notify(hint, timeout=3)
            return
        self._set_action(branch, Action.DELETE_LOCAL)

    def action_open_compare(self) -> None:
        branch = self._cursor_branch()
        if branch is None:
            return
        if self._compare_url is None:
            self.notify("origin has no web URL to link to", severity="warning", timeout=3)
            return
        if branch.is_default:
            self.notify(f"{branch.name} is the compare base", severity="warning", timeout=3)
            return
        if not branch.has_remote:
            self.notify(f"{branch.name} is not on origin", severity="warning", timeout=3)
            return
        self.open_url(self._compare_url(branch.name))

    # ---------- worktree actions ----------

    def _cursor_worktree(self) -> WorktreeInfo | None:
        key = self._cursor_key(self._worktree_table)
        return self._by_path.get(key or "")

    @staticmethod
    def _unmarkable_reason(wt: WorktreeInfo) -> str:
        label = format_worktree_path(wt.path)
        if wt.is_main:
            return f"{label} is the main worktree — git cannot remove it"
        if wt.is_current:
            return f"{label} is the worktree you are in — git cannot remove it"
        detail = f": {wt.lock_reason}" if wt.lock_reason else ""
        return f"{label} is locked{detail} — unlock it first"

    def _set_worktree_action(self, wt: WorktreeInfo, action: WorktreeAction) -> None:
        if wt.name not in self.worktree_actions:
            self.notify(self._unmarkable_reason(wt), severity="warning", timeout=4)
            return
        self.worktree_actions[wt.name] = action
        self._worktree_table.update_cell(
            wt.name, "action", self._worktree_action_cell(wt.name), update_width=False
        )
        self._refresh_status()

    def action_mark_worktree(self, action: str) -> None:
        wt = self._cursor_worktree()
        if wt:
            self._set_worktree_action(wt, WorktreeAction(action))

    def action_cycle_worktree(self) -> None:
        wt = self._cursor_worktree()
        if wt is None:
            return
        current = self.worktree_actions.get(wt.name, WorktreeAction.KEEP)
        following = (
            WorktreeAction.REMOVE if current is WorktreeAction.KEEP else WorktreeAction.KEEP
        )
        self._set_worktree_action(wt, following)

    # ---------- review ----------

    def _outcome(self) -> Outcome:
        return Outcome(
            branches=[
                (self._by_name[name], action)
                for name, action in self.actions.items()
                if action is not Action.KEEP
            ],
            worktrees=[
                (self._by_path[name], action)
                for name, action in self.worktree_actions.items()
                if action is not WorktreeAction.KEEP
            ],
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._open_review()

    def _open_review(self) -> None:
        outcome = self._outcome()
        if not outcome:
            hint = (
                "Nothing marked — use space/d to mark worktrees"
                if self._active_tab == TAB_WORKTREES
                else "Nothing marked — use space/d/a to mark branches"
            )
            self.notify(hint, timeout=3)
            return

        def handle(confirmed: bool | None) -> None:
            if confirmed:
                self.exit(outcome)

        self.push_screen(ReviewScreen(outcome, self._dry_run), handle)

    def action_quit_nochange(self) -> None:
        self.exit(None)

    # ---------- filter / sort inputs ----------

    def _notify_view_change(self) -> None:
        if self._on_view_change is not None:
            self._on_view_change(self._filter_spec, planner.format_sort(self._sort_fields))

    def action_reset_view(self) -> None:
        self._filter_spec = ""
        self._sort_fields = planner.parse_sort(planner.DEFAULT_SORT)
        self._visible = self._apply_view(self._all)
        self._rebuild_table()
        self._notify_view_change()
        self.notify("Filter and sort reset", timeout=3)

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
        self._open_input("sort", planner.format_sort(self._sort_fields), "sort: e.g. -age,author")

    def close_spec_input(self) -> None:
        spec_input = self.query_one("#spec-input", SpecInput)
        spec_input.display = False
        self._input_mode = ""
        self._branch_table.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        mode, value = self._input_mode, event.value.strip()
        try:
            if mode == "filter":
                planner.parse_filter(value)  # validate before adopting
                self._filter_spec = value
            elif mode == "sort":
                self._sort_fields = planner.parse_sort(value or planner.DEFAULT_SORT)
        except ValueError as exc:
            self.notify(str(exc), severity="error", timeout=5)
            return
        self._visible = self._apply_view(self._all)
        self.close_spec_input()
        self._rebuild_table()
        self._notify_view_change()
