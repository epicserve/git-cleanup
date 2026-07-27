"""Headless Textual tests via app.run_test()/Pilot (no git, no terminal)."""

from datetime import UTC, datetime, timedelta

from rich.text import Text
from textual.widgets import DataTable, Static

from git_cleanup import planner
from git_cleanup.models import Action, BranchInfo
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
        # cursor starts on row 0: abc-1-merged (DELETE)
        await pilot.press("space")
        assert app.actions["abc-1-merged"] is Action.ARCHIVE
        await pilot.press("space")
        assert app.actions["abc-1-merged"] is Action.KEEP
        await pilot.press("space")
        assert app.actions["abc-1-merged"] is Action.DELETE


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

        table = app.query_one(DataTable)
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
        assert app.query_one(DataTable).row_count == 5
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
        assert app.query_one(DataTable).row_count == 5


async def test_enter_review_confirm_returns_decisions():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("enter")
        assert isinstance(app.screen, ReviewScreen)
        await pilot.press("y")
    assert [(b.name, a) for b, a in app.return_value] == [("abc-1-merged", Action.DELETE)]


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
        assert app.query_one(DataTable).row_count == 4  # zz-theirs hidden
        await pilot.press("r")
        assert app._filter_spec == ""
        assert app._sort_fields == planner.parse_sort("branch")
        assert app.query_one(DataTable).row_count == 5


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


async def test_no_dry_run_no_banner():
    app = make_app()
    async with app.run_test():
        assert not app.query_one("#status", Static).has_class("dry-run")
        assert "DRY RUN" not in status_text(app)
