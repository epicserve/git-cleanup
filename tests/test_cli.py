from pathlib import Path

import pytest

from git_cleanup import cli, planner, state, tui
from git_cleanup.models import Action
from tests.conftest import git


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch):
    """Keep persisted view state away from the developer's real ~/.local/state."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


@pytest.fixture
def no_tracker_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text('[tracker]\nprovider = "none"\n')
    return path


@pytest.fixture
def accept_recommended(monkeypatch):
    """Simulate a TUI session that confirms every recommendation, plus
    archiving old-experiment (the per-branch archive choice)."""
    seen: dict[str, list] = {"branches": [], "kwargs": []}

    def fake_run_tui(branches, *, my_email, include_all, archive_age_days, **kwargs):
        seen["branches"].append([b.name for b in branches])
        seen["kwargs"].append(kwargs)
        recommended = planner.recommend_actions(
            branches,
            for_email=my_email,
            include_all=include_all,
            archive_age_days=archive_age_days,
        )
        by_name = {b.name: b for b in branches}
        return [
            (by_name[name], action)
            for name, action in recommended.items()
            if action is Action.DELETE or name == "old-experiment"
        ]

    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(tui, "run_tui", fake_run_tui)  # cli.tui is this module
    return seen


def run_cli(*argv: str) -> int:
    args = cli.build_parser().parse_args(list(argv))
    return cli.run(args)


def test_full_run_deletes_and_archives(repo, no_tracker_config, accept_recommended, monkeypatch):
    monkeypatch.chdir(repo)
    code = run_cli("--config", str(no_tracker_config))
    assert code == 0

    # my merged branch deleted locally and on origin
    assert git("branch", "--list", "abc-123-fix-login", cwd=repo) == ""
    assert git("ls-remote", "--heads", "origin", "abc-123-fix-login", cwd=repo) == ""
    # old local-only branch archived (tag created, branch gone)
    assert "archive/old-experiment" in git("tag", "--list", "archive/*", cwd=repo)
    assert git("branch", "--list", "old-experiment", cwd=repo) == ""

    # untouched: default branch, unmerged recent work, other author's branch
    assert git("branch", "--list", "main", cwd=repo) != ""
    assert git("branch", "--list", "abc-201-new-dashboard", cwd=repo) != ""
    assert git("ls-remote", "--heads", "origin", "abc-99-hotfix", cwd=repo) != ""


def test_delete_local_leaves_origin_alone(repo, no_tracker_config, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    def fake_run_tui(branches, **kwargs):
        by_name = {b.name: b for b in branches}
        return [(by_name["abc-123-fix-login"], Action.DELETE_LOCAL)]

    monkeypatch.setattr(tui, "run_tui", fake_run_tui)
    assert run_cli("--config", str(no_tracker_config)) == 0

    assert git("branch", "--list", "abc-123-fix-login", cwd=repo) == ""
    assert git("ls-remote", "--heads", "origin", "abc-123-fix-login", cwd=repo) != ""
    assert "deleted 1 local, 0 remote" in capsys.readouterr().out


def test_dry_run_changes_nothing(repo, no_tracker_config, accept_recommended, monkeypatch):
    monkeypatch.chdir(repo)
    code = run_cli("--dry-run", "--config", str(no_tracker_config))
    assert code == 0

    assert git("branch", "--list", "abc-123-fix-login", cwd=repo) != ""
    assert git("ls-remote", "--heads", "origin", "abc-123-fix-login", cwd=repo) != ""
    assert git("branch", "--list", "old-experiment", cwd=repo) != ""
    assert git("tag", "--list", "archive/*", cwd=repo) == ""


def test_all_flag_includes_other_authors_remote(
    repo, no_tracker_config, accept_recommended, monkeypatch
):
    monkeypatch.chdir(repo)
    code = run_cli("--all", "--config", str(no_tracker_config))
    assert code == 0
    # sarah's merged remote-only branch is recommended and deleted with --all
    assert git("ls-remote", "--heads", "origin", "abc-99-hotfix", cwd=repo) == ""


def test_quit_tui_changes_nothing(repo, no_tracker_config, monkeypatch):
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.tui, "run_tui", lambda *a, **kw: None)
    code = run_cli("--config", str(no_tracker_config))
    assert code == 0
    assert git("branch", "--list", "abc-123-fix-login", cwd=repo) != ""
    assert git("ls-remote", "--heads", "origin", "abc-123-fix-login", cwd=repo) != ""


def test_non_tty_skips_tui_and_changes_nothing(repo, no_tracker_config, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    # no _interactive patch: pytest's stdin is not a TTY
    code = run_cli("--config", str(no_tracker_config))
    assert code == 0
    assert git("branch", "--list", "abc-123-fix-login", cwd=repo) != ""
    out = capsys.readouterr().out
    # overview table printed (rich may fold long names, so match short pieces)
    assert "Branches" in out and "abc-123" in out


def test_invalid_sort_fails_fast(repo, no_tracker_config, monkeypatch):
    monkeypatch.chdir(repo)
    assert run_cli("--sort", "bogus", "--config", str(no_tracker_config)) == 2


def test_sort_flag_accepted(repo, no_tracker_config, accept_recommended, monkeypatch):
    monkeypatch.chdir(repo)
    # descending specs must use the = form: --sort=-age (argparse reads a bare
    # "-age" token as a flag)
    assert run_cli("--dry-run", "--sort=-age,author", "--config", str(no_tracker_config)) == 0


def test_invalid_filter_fails_fast(repo, no_tracker_config, monkeypatch):
    monkeypatch.chdir(repo)
    assert run_cli("--filter", "age>abc", "--config", str(no_tracker_config)) == 2


def test_filter_passed_to_tui_with_all_branches(
    repo, no_tracker_config, accept_recommended, monkeypatch
):
    monkeypatch.chdir(repo)
    code = run_cli("--filter", "author=nobody", "--config", str(no_tracker_config))
    assert code == 0
    # the TUI gets every branch (so the filter can be loosened in-session)
    # plus the spec to apply as its initial view
    assert len(accept_recommended["branches"][0]) == 6
    assert accept_recommended["kwargs"][0]["filter_spec"] == "author=nobody"


def test_persisted_view_used_when_no_flags(
    repo, no_tracker_config, accept_recommended, monkeypatch
):
    monkeypatch.chdir(repo)
    root = Path(git("rev-parse", "--show-toplevel", cwd=repo)).resolve()
    state.save_repo_state(root, {"filter": "mine", "sort": "-age"})
    code = run_cli("--dry-run", "--config", str(no_tracker_config))
    assert code == 0
    kwargs = accept_recommended["kwargs"][0]
    assert kwargs["filter_spec"] == "mine"
    assert kwargs["sort_fields"] == planner.parse_sort("-age")


def test_explicit_flags_beat_persisted_view(
    repo, no_tracker_config, accept_recommended, monkeypatch
):
    monkeypatch.chdir(repo)
    root = Path(git("rev-parse", "--show-toplevel", cwd=repo)).resolve()
    state.save_repo_state(root, {"filter": "mine", "sort": "-age"})
    code = run_cli("--dry-run", "--filter", "author=nobody", "--config", str(no_tracker_config))
    assert code == 0
    kwargs = accept_recommended["kwargs"][0]
    assert kwargs["filter_spec"] == "author=nobody"
    assert kwargs["sort_fields"] == planner.parse_sort("-age")  # sort still persisted


def test_invalid_persisted_view_warns_and_defaults(
    repo, no_tracker_config, accept_recommended, monkeypatch, capsys
):
    monkeypatch.chdir(repo)
    root = Path(git("rev-parse", "--show-toplevel", cwd=repo)).resolve()
    state.save_repo_state(root, {"filter": "age>abc", "sort": "bogus"})
    code = run_cli("--dry-run", "--config", str(no_tracker_config))
    assert code == 0
    kwargs = accept_recommended["kwargs"][0]
    assert kwargs["filter_spec"] == ""
    assert kwargs["sort_fields"] == planner.parse_sort("branch")
    out = capsys.readouterr().out
    assert "ignoring saved filter" in out and "ignoring saved sort" in out


def test_view_changes_persist_to_state_file(repo, no_tracker_config, monkeypatch):
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    def fake_run_tui(branches, **kwargs):
        kwargs["on_view_change"]("mine", "-age")
        return []

    monkeypatch.setattr(tui, "run_tui", fake_run_tui)
    assert run_cli("--config", str(no_tracker_config)) == 0
    root = Path(git("rev-parse", "--show-toplevel", cwd=repo)).resolve()
    assert state.load_repo_state(root) == {"filter": "mine", "sort": "-age"}


def test_non_interactive_ignores_persisted_view(repo, no_tracker_config, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    root = Path(git("rev-parse", "--show-toplevel", cwd=repo)).resolve()
    state.save_repo_state(root, {"filter": "author=nobody"})
    # no _interactive patch: pytest's stdin is not a TTY
    code = run_cli("--config", str(no_tracker_config))
    assert code == 0
    out = capsys.readouterr().out
    assert "abc-123" in out  # saved filter not applied to CI/pipe output


def test_outside_repo_fails_cleanly(tmp_path, monkeypatch):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert run_cli() == 1
