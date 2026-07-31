"""Headless Textual tests via app.run_test()/Pilot (no git, no terminal)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.text import Text
from textual.widgets import Button, DataTable, Static, Tabs

from git_cleanup import planner
from git_cleanup.models import Action, BranchInfo, WorktreeAction, WorktreeInfo
from git_cleanup.tui import CleanupApp, ReviewScreen, SpecInput

ME = "brent@example.com"
OTHER = "sarah@example.com"


def make_branch(name: str, **overrides) -> BranchInfo:
    defaults = dict(
        name=name,
        has_local=True,
        has_remote=True,
        sha=f"sha-{name}",
        author_name="Brent",
        author_email=ME,
        committed_at=datetime.now(UTC) - timedelta(days=5),
        merged=False,
        ahead=0,
        behind=0,
    )
    defaults.update(overrides)
    return BranchInfo(**defaults)


def default_branches() -> list[BranchInfo]:
    return [
        make_branch("abc-1-merged", merged=True),
        make_branch("abc-2-open"),
        make_branch("main", merged=True, is_current=True, is_default=True, is_protected=True),
        make_branch(
            "old-thing",
            has_remote=False,
            committed_at=datetime.now(UTC) - timedelta(days=200),
        ),
        make_branch(
            "zz-theirs",
            has_local=False,
            merged=True,
            author_name="Sarah",
            author_email=OTHER,
            ahead=None,
            behind=None,
        ),
    ]


def make_app(**overrides) -> CleanupApp:
    kwargs = dict(
        my_email=ME,
        include_all=False,
        archive_age_days=90,
        sort_fields=planner.parse_sort("branch"),
        dry_run=False,
    )
    branches = overrides.pop("branches", None) or default_branches()
    kwargs["worktrees"] = overrides.pop("worktrees", [])
    kwargs.update(overrides)
    return CleanupApp(branches, **kwargs)


async def test_premarks():
    app = make_app()
    async with app.run_test():
        assert app.actions == {
            "abc-1-merged": Action.DELETE,  # mine + merged
            "abc-2-open": Action.KEEP,
            "old-thing": Action.KEEP,  # archive is never pre-marked
            "zz-theirs": Action.KEEP,  # not mine without --all
        }
        assert "main" not in app.actions  # protected


async def test_include_all_premarks_others():
    app = make_app(include_all=True)
    async with app.run_test():
        assert app.actions["zz-theirs"] is Action.DELETE


async def test_space_cycles_action():
    app = make_app()
    async with app.run_test() as pilot:
        # cursor starts on row 0: abc-1-merged (DELETE), local + remote
        await pilot.press("space")
        assert app.actions["abc-1-merged"] is Action.DELETE_LOCAL
        await pilot.press("space")
        assert app.actions["abc-1-merged"] is Action.ARCHIVE
        await pilot.press("space")
        assert app.actions["abc-1-merged"] is Action.KEEP
        await pilot.press("space")
        assert app.actions["abc-1-merged"] is Action.DELETE


async def test_space_skips_delete_local_for_single_sided_branch():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("down", "down", "down")  # old-thing: local only
        await pilot.press("space")
        assert app.actions["old-thing"] is Action.DELETE
        await pilot.press("space")
        assert app.actions["old-thing"] is Action.ARCHIVE


async def test_explicit_mark_keys():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("down")  # abc-2-open
        await pilot.press("a")
        assert app.actions["abc-2-open"] is Action.ARCHIVE
        await pilot.press("d")
        assert app.actions["abc-2-open"] is Action.DELETE
        await pilot.press("k")
        assert app.actions["abc-2-open"] is Action.KEEP


async def test_d_toggles_between_delete_and_delete_local():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("down")  # abc-2-open: local + remote
        await pilot.press("d")
        assert app.actions["abc-2-open"] is Action.DELETE  # first press: both sides
        await pilot.press("d")
        assert app.actions["abc-2-open"] is Action.DELETE_LOCAL
        await pilot.press("d")
        assert app.actions["abc-2-open"] is Action.DELETE  # toggles back
        await pilot.press("k")
        await pilot.press("d")
        assert app.actions["abc-2-open"] is Action.DELETE  # re-entering starts at both


async def test_d_stays_on_delete_for_single_sided_branches():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("down", "down", "down")  # old-thing: local only
        await pilot.press("d", "d")
        assert app.actions["old-thing"] is Action.DELETE

        await pilot.press("down")  # zz-theirs: remote only
        await pilot.press("d", "d")
        assert app.actions["zz-theirs"] is Action.DELETE


async def test_protected_row_rejected():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("down", "down")  # main
        await pilot.press("d")
        assert "main" not in app.actions


async def test_filter_keeps_hidden_marks():
    app = make_app()
    async with app.run_test() as pilot:
        # mark sarah's branch for deletion, then filter it out
        await pilot.press("down", "down", "down", "down")  # zz-theirs
        await pilot.press("d")
        assert app.actions["zz-theirs"] is Action.DELETE

        await pilot.press("slash")
        spec_input = app.query_one("#spec-input", SpecInput)
        assert spec_input.display
        spec_input.value = "mine"
        await pilot.press("enter")

        table = app.query_one("#branch-table", DataTable)
        assert table.row_count == 4  # zz-theirs hidden
        assert app.actions["zz-theirs"] is Action.DELETE  # mark survives


async def test_sort_reorders():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("s")
        spec_input = app.query_one("#spec-input", SpecInput)
        spec_input.value = "-age"
        await pilot.press("enter")
        assert app._visible[0].name == "old-thing"


async def test_invalid_spec_leaves_view_unchanged():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("slash")
        spec_input = app.query_one("#spec-input", SpecInput)
        spec_input.value = "age>abc"
        await pilot.press("enter")
        assert app._filter_spec == ""
        assert app.query_one("#branch-table", DataTable).row_count == 5
        assert spec_input.display  # stays open for correction


async def test_escape_closes_input_without_applying():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("slash")
        spec_input = app.query_one("#spec-input", SpecInput)
        spec_input.value = "mine"
        await pilot.press("escape")
        assert not spec_input.display
        assert app._filter_spec == ""
        assert app.query_one("#branch-table", DataTable).row_count == 5


async def test_enter_review_confirm_returns_decisions():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("enter")
        assert isinstance(app.screen, ReviewScreen)
        await pilot.press("y")
    assert [(b.name, a) for b, a in app.return_value.branches] == [("abc-1-merged", Action.DELETE)]


def review_text(app: CleanupApp) -> str:
    return "\n".join(
        widget.content.plain if isinstance(widget.content, Text) else str(widget.content)
        for widget in app.screen.query(Static)
    )


async def test_review_warns_about_origin_for_plain_delete():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("enter")  # abc-1-merged is pre-marked DELETE
        assert app.screen.query(".remote-warning")
        assert "deleted for everyone" in review_text(app)


async def test_review_omits_origin_warning_for_delete_local():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("d")  # pre-marked DELETE -> DELETE_LOCAL
        assert app.actions["abc-1-merged"] is Action.DELETE_LOCAL
        await pilot.press("enter")
        assert isinstance(app.screen, ReviewScreen)
        assert not app.screen.query(".remote-warning")
        body = review_text(app)
        assert "Delete 1 local:" in body
        assert "keeping origin/abc-1-merged" in body


async def test_review_confirm_returns_delete_local_decision():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("d")
        await pilot.press("enter")
        await pilot.press("y")
    assert [(b.name, a) for b, a in app.return_value.branches] == [
        ("abc-1-merged", Action.DELETE_LOCAL)
    ]


async def test_review_cancel_returns_to_table():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("enter")
        assert isinstance(app.screen, ReviewScreen)
        await pilot.press("n")
        assert not isinstance(app.screen, ReviewScreen)
        assert app.return_value is None
        assert app.is_running


async def test_quit_returns_none():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("q")
    assert app.return_value is None


async def test_enter_with_nothing_marked_stays():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("k")  # unmark the one pre-marked row
        assert all(a is Action.KEEP for a in app.actions.values())
        await pilot.press("enter")
        assert not isinstance(app.screen, ReviewScreen)
        assert app.is_running


async def test_enter_binding_advertised_in_footer():
    app = make_app()
    async with app.run_test():
        active = app.screen.active_bindings["enter"]
        assert active.binding.show
        assert active.binding.description == "Review"


async def test_reset_view_clears_filter_and_sort():
    app = make_app(filter_spec="mine", sort_fields=planner.parse_sort("-age"))
    async with app.run_test() as pilot:
        assert app.query_one("#branch-table", DataTable).row_count == 4  # zz-theirs hidden
        await pilot.press("r")
        assert app._filter_spec == ""
        assert app._sort_fields == planner.parse_sort("branch")
        assert app.query_one("#branch-table", DataTable).row_count == 5


async def test_view_change_callback_on_filter_and_sort():
    changes: list[tuple[str, str]] = []
    app = make_app(on_view_change=lambda f, s: changes.append((f, s)))
    async with app.run_test() as pilot:
        await pilot.press("slash")
        app.query_one("#spec-input", SpecInput).value = "mine"
        await pilot.press("enter")
        assert changes[-1] == ("mine", "branch")

        await pilot.press("s")
        app.query_one("#spec-input", SpecInput).value = "-age"
        await pilot.press("enter")
        assert changes[-1] == ("mine", "-age")


async def test_invalid_spec_does_not_fire_callback():
    changes: list[tuple[str, str]] = []
    app = make_app(on_view_change=lambda f, s: changes.append((f, s)))
    async with app.run_test() as pilot:
        await pilot.press("slash")
        app.query_one("#spec-input", SpecInput).value = "age>abc"
        await pilot.press("enter")
        assert changes == []


async def test_reset_fires_callback():
    changes: list[tuple[str, str]] = []
    app = make_app(
        filter_spec="mine",
        sort_fields=planner.parse_sort("-age"),
        on_view_change=lambda f, s: changes.append((f, s)),
    )
    async with app.run_test() as pilot:
        await pilot.press("r")
        assert changes == [("", "branch")]


async def test_o_opens_compare_for_remote_branch():
    opened: list[str] = []
    app = make_app(compare_url=lambda name: f"https://example.com/compare/main...{name}")
    app.open_url = opened.append
    async with app.run_test() as pilot:
        await pilot.press("o")  # cursor on abc-1-merged, which is on origin
        assert opened == ["https://example.com/compare/main...abc-1-merged"]


async def test_o_skips_default_and_local_only_branches():
    opened: list[str] = []
    app = make_app(compare_url=lambda name: name)
    app.open_url = opened.append
    async with app.run_test() as pilot:
        await pilot.press("down", "down")  # main: the compare base itself
        await pilot.press("o")
        await pilot.press("down")  # old-thing: not on origin
        await pilot.press("o")
        assert opened == []


async def test_o_without_web_url_is_noop():
    opened: list[str] = []
    app = make_app()  # compare_url omitted: origin has no web URL
    app.open_url = opened.append
    async with app.run_test() as pilot:
        await pilot.press("o")
        assert opened == []
        assert app.is_running


def status_text(app: CleanupApp) -> str:
    content = app.query_one("#status", Static).content
    assert isinstance(content, Text)
    return content.plain


async def test_dry_run_status_prominent():
    app = make_app(dry_run=True)
    async with app.run_test():
        status = app.query_one("#status", Static)
        assert status.has_class("dry-run")
        assert status_text(app).startswith("DRY RUN — nothing will change")


async def test_status_counts_delete_local_separately():
    app = make_app()
    async with app.run_test() as pilot:
        assert "1 delete" in status_text(app)
        assert "delete-local" not in status_text(app)  # hidden while zero
        await pilot.press("d")  # abc-1-merged: DELETE -> DELETE_LOCAL
        status = status_text(app)
        assert "0 delete" in status and "1 delete-local" in status


async def test_no_dry_run_no_banner():
    app = make_app()
    async with app.run_test():
        assert not app.query_one("#status", Static).has_class("dry-run")
        assert "DRY RUN" not in status_text(app)


# ---------- worktrees ----------


def make_worktree(path: str, branch: str | None = None, **overrides) -> WorktreeInfo:
    defaults = dict(
        path=Path(path),
        head="deadbeefcafe1234",
        branch=f"refs/heads/{branch}" if branch else None,
        dirty_count=0,
    )
    defaults.update(overrides)
    return WorktreeInfo(**defaults)


def worktree_fixture() -> tuple[list[BranchInfo], list[WorktreeInfo]]:
    """Branches plus worktrees covering main / clean-merged / dirty-merged /
    open / locked / prunable, wired up the way planner.build_worktrees would."""
    branches = [
        make_branch("main", merged=True, is_current=True, is_default=True, is_protected=True),
        make_branch("wt-clean", merged=True),
        make_branch("wt-dirty", merged=True),
        make_branch("wt-locked", merged=True),
        make_branch("wt-open"),
    ]
    by_name = {b.name: b for b in branches}
    worktrees = [
        make_worktree("/repo", "main", is_main=True, is_current=True, branch_info=by_name["main"]),
        make_worktree("/wt/clean", "wt-clean", branch_info=by_name["wt-clean"]),
        make_worktree("/wt/dirty", "wt-dirty", dirty_count=2, branch_info=by_name["wt-dirty"]),
        make_worktree(
            "/wt/locked",
            "wt-locked",
            locked=True,
            lock_reason="on a network share",
            branch_info=by_name["wt-locked"],
        ),
        make_worktree("/wt/open", "wt-open", branch_info=by_name["wt-open"]),
        make_worktree(
            "/wt/gone",
            None,
            detached=True,
            prunable=True,
            dirty_count=None,
            prune_reason="gitdir file points to non-existent location",
        ),
    ]
    # what build_worktrees back-fills
    for worktree in worktrees:
        if worktree.branch_info is not None:
            worktree.branch_info.worktree_path = worktree.path
    return branches, worktrees


def worktree_app(**overrides) -> CleanupApp:
    branches, worktrees = worktree_fixture()
    return make_app(branches=branches, worktrees=worktrees, **overrides)


def clear_branch_marks(app: CleanupApp) -> None:
    """Several fixture branches are merged+mine and so come pre-marked DELETE;
    tests that assert on one branch clear the rest first."""
    for name in list(app.actions):
        app.actions[name] = Action.KEEP


def clear_worktree_marks(app: CleanupApp) -> None:
    for name in list(app.worktree_actions):
        app.worktree_actions[name] = WorktreeAction.KEEP


async def to_worktrees(app: CleanupApp, pilot) -> None:
    """Tab activation is message-driven, so the pause is required."""
    await pilot.press("w")
    await pilot.pause()


def worktree_table(app: CleanupApp) -> DataTable:
    return app.query_one("#worktree-table", DataTable)


async def test_worktree_premarks():
    app = worktree_app()
    async with app.run_test():
        assert app.worktree_actions == {
            "/wt/clean": WorktreeAction.REMOVE,  # merged + clean
            "/wt/dirty": WorktreeAction.KEEP,  # merged but dirty
            "/wt/open": WorktreeAction.KEEP,
            "/wt/gone": WorktreeAction.REMOVE,  # directory is already gone
        }
        # git cannot remove these, so they are not markable at all
        assert "/repo" not in app.worktree_actions
        assert "/wt/locked" not in app.worktree_actions


async def test_dirty_worktree_is_never_premarked():
    """Pre-marking must not set up a --force that discards uncommitted work."""
    app = worktree_app()
    async with app.run_test():
        assert app.worktree_actions["/wt/dirty"] is WorktreeAction.KEEP


async def test_w_focuses_worktree_table():
    """Guards gotcha A: ContentSwitcher drops focus when it hides the old pane,
    and with no focus every row binding dies and the footer empties."""
    app = worktree_app()
    async with app.run_test() as pilot:
        await to_worktrees(app, pilot)
        assert app.focused is worktree_table(app)
        assert app._active_tab == "tab-worktrees"

        await pilot.press("b")
        await pilot.pause()
        assert app.focused is app.query_one("#branch-table", DataTable)


async def test_tab_key_does_not_steal_focus():
    """Guards gotcha B: focus landing on the tab bar would kill row bindings."""
    app = worktree_app()
    async with app.run_test() as pilot:
        await to_worktrees(app, pilot)
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is worktree_table(app)
        assert not app.query_one(Tabs).can_focus


async def test_footer_keys_follow_the_active_tab():
    app = worktree_app()
    async with app.run_test() as pilot:
        assert app.screen.active_bindings["d"].binding.description == "Delete (again: local)"
        assert "slash" in app.screen.active_bindings

        await to_worktrees(app, pilot)
        assert app.screen.active_bindings["d"].binding.description == "Remove"
        # decision 3 enforces itself: filter/sort are simply not in the chain
        assert "slash" not in app.screen.active_bindings
        assert "s" not in app.screen.active_bindings
        assert "o" not in app.screen.active_bindings
        assert "a" not in app.screen.active_bindings


async def test_active_tabs_own_key_is_disabled():
    app = worktree_app()
    async with app.run_test() as pilot:
        assert not app.screen.active_bindings["b"].enabled  # already on Branches
        assert app.screen.active_bindings["w"].enabled

        await to_worktrees(app, pilot)
        assert app.screen.active_bindings["b"].enabled
        assert not app.screen.active_bindings["w"].enabled


async def test_branch_keys_are_inert_on_the_worktrees_tab():
    app = worktree_app()
    async with app.run_test() as pilot:
        before = dict(app.actions)
        await to_worktrees(app, pilot)
        await pilot.press("a")  # archive: a branch key
        assert app.actions == before


async def test_mark_and_toggle_worktree():
    app = worktree_app()
    async with app.run_test() as pilot:
        await to_worktrees(app, pilot)
        await pilot.press("down", "down", "down", "down")  # /wt/open (row 4)
        await pilot.press("d")
        assert app.worktree_actions["/wt/open"] is WorktreeAction.REMOVE
        await pilot.press("k")
        assert app.worktree_actions["/wt/open"] is WorktreeAction.KEEP
        await pilot.press("space")
        assert app.worktree_actions["/wt/open"] is WorktreeAction.REMOVE
        await pilot.press("space")
        assert app.worktree_actions["/wt/open"] is WorktreeAction.KEEP


async def test_main_worktree_cannot_be_marked():
    app = worktree_app()
    async with app.run_test() as pilot:
        await to_worktrees(app, pilot)
        await pilot.press("d")  # row 0 is the main worktree
        assert "/repo" not in app.worktree_actions
        assert app.is_running


async def test_locked_worktree_cannot_be_marked():
    app = worktree_app()
    async with app.run_test() as pilot:
        await to_worktrees(app, pilot)
        await pilot.press("down", "down", "down")  # /wt/locked (row 3)
        assert app._cursor_worktree().path == Path("/wt/locked")
        await pilot.press("d")
        assert "/wt/locked" not in app.worktree_actions
        assert app.is_running


async def test_dirty_worktree_can_be_marked_manually():
    app = worktree_app()
    async with app.run_test() as pilot:
        await to_worktrees(app, pilot)
        await pilot.press("down", "down")  # /wt/dirty (row 2)
        await pilot.press("d")
        assert app.worktree_actions["/wt/dirty"] is WorktreeAction.REMOVE


async def test_worktree_status_counts():
    app = worktree_app()
    async with app.run_test() as pilot:
        await to_worktrees(app, pilot)
        status = status_text(app)
        assert "6 worktrees" in status
        assert "1 remove" in status  # /wt/clean
        assert "1 prune" in status  # /wt/gone
        assert "dirty" not in status  # nothing dirty is marked yet

        await pilot.press("down", "down")  # /wt/dirty
        await pilot.press("d")
        assert "1 dirty — will force" in status_text(app)


async def test_status_shows_cross_tab_marks():
    app = worktree_app()
    async with app.run_test() as pilot:
        # branches tab: worktree marks are pre-set, so the tail shows them
        assert "2 worktrees marked" in status_text(app)

        await to_worktrees(app, pilot)
        assert "branches marked" in status_text(app)


async def test_dry_run_banner_survives_a_tab_switch():
    app = worktree_app(dry_run=True)
    async with app.run_test() as pilot:
        await to_worktrees(app, pilot)
        assert status_text(app).startswith("DRY RUN — nothing will change")
        assert app.query_one("#status", Static).has_class("dry-run")


async def test_branches_tab_shows_wt_column():
    app = worktree_app()
    async with app.run_test():
        table = app.query_one("#branch-table", DataTable)
        labels = [str(col.label) for col in table.columns.values()]
        assert labels.index("WT") == labels.index("Remote") + 1


async def test_enter_from_worktrees_tab_opens_review():
    app = worktree_app()
    async with app.run_test() as pilot:
        await to_worktrees(app, pilot)
        await pilot.press("enter")
        assert isinstance(app.screen, ReviewScreen)


async def test_review_lists_worktree_sections():
    app = worktree_app()
    async with app.run_test() as pilot:
        await pilot.press("enter")
        body = review_text(app)
        assert "Remove 1 worktrees:" in body
        # decision 1, made legible right where a user would assume otherwise
        assert "→ branch wt-clean stays" in body
        # never tell a user a missing directory is being "removed"
        assert "Clear 1 broken entries:" in body
        assert "→ prune (directory is gone)" in body


async def test_review_flags_dirty_removals_in_a_red_panel():
    app = worktree_app()
    async with app.run_test() as pilot:
        await to_worktrees(app, pilot)
        await pilot.press("down", "down")  # /wt/dirty
        await pilot.press("d")
        await pilot.press("enter")
        assert app.screen.query(".worktree-warning")
        body = review_text(app)
        assert "uncommitted changes" in body
        assert "the changes are not recoverable" in body
        confirm = app.screen.query_one("#confirm", Button)
        assert confirm.variant == "error"


async def test_review_warns_branch_delete_will_fail_without_worktree_removal():
    branches, worktrees = worktree_fixture()
    app = make_app(branches=branches, worktrees=worktrees)
    async with app.run_test() as pilot:
        clear_worktree_marks(app)  # nothing is being removed
        clear_branch_marks(app)
        # wt-dirty's branch is marked delete but its worktree is not being removed
        app.actions["wt-dirty"] = Action.DELETE
        await pilot.press("enter")
        body = review_text(app)
        assert "✗ checked out in /wt/dirty — delete will fail" in body
        assert "after removing worktree" not in body


async def test_review_note_flips_once_the_worktree_is_marked():
    branches, worktrees = worktree_fixture()
    app = make_app(branches=branches, worktrees=worktrees)
    async with app.run_test() as pilot:
        clear_branch_marks(app)
        app.actions["wt-clean"] = Action.DELETE  # its worktree is pre-marked remove
        await pilot.press("enter")
        body = review_text(app)
        assert "→ after removing worktree /wt/clean" in body
        assert "delete will fail" not in body


async def test_review_annotates_the_archive_group_too():
    """_archive force-deletes the local branch, and git refuses that identically."""
    branches, worktrees = worktree_fixture()
    app = make_app(branches=branches, worktrees=worktrees)
    async with app.run_test() as pilot:
        clear_worktree_marks(app)
        clear_branch_marks(app)
        app.actions["wt-open"] = Action.ARCHIVE
        await pilot.press("enter")
        body = review_text(app)
        assert "Archive 1:" in body
        assert "✗ checked out in /wt/open — delete will fail" in body


async def test_confirm_returns_outcome_with_both_kinds():
    branches, worktrees = worktree_fixture()
    app = make_app(branches=branches, worktrees=worktrees)
    async with app.run_test() as pilot:
        clear_branch_marks(app)
        app.actions["wt-open"] = Action.DELETE
        await pilot.press("enter")
        await pilot.press("y")
    outcome = app.return_value
    assert [(b.name, a) for b, a in outcome.branches] == [("wt-open", Action.DELETE)]
    assert sorted((str(wt.path), a) for wt, a in outcome.worktrees) == [
        ("/wt/clean", WorktreeAction.REMOVE),
        ("/wt/gone", WorktreeAction.REMOVE),
    ]


async def test_worktrees_marked_alone_can_be_confirmed():
    app = worktree_app()
    async with app.run_test() as pilot:
        clear_branch_marks(app)
        await to_worktrees(app, pilot)
        await pilot.press("enter")
        assert isinstance(app.screen, ReviewScreen)
        await pilot.press("y")
    assert app.return_value.branches == []
    assert len(app.return_value.worktrees) == 2


async def test_empty_worktree_list_is_harmless():
    """Guards the worktrees=() default that a plain repo hits."""
    app = make_app()
    async with app.run_test() as pilot:
        await to_worktrees(app, pilot)
        assert worktree_table(app).row_count == 0
        assert app.worktree_actions == {}
        await pilot.press("d")  # no cursor row: must not raise
        await pilot.press("enter")  # nothing marked on this tab
        assert app.is_running
        assert "0 worktrees" in status_text(app)
