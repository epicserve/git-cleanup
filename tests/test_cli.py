from pathlib import Path

import pytest

from git_cleanup import cli, planner, tui
from git_cleanup.models import Action
from tests.conftest import git


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


def test_filter_restricts_tui_branches(repo, no_tracker_config, accept_recommended, monkeypatch):
    monkeypatch.chdir(repo)
    code = run_cli("--filter", "author=nobody", "--config", str(no_tracker_config))
    assert code == 0
    # nothing matched the filter, so the TUI received no branches to act on
    assert accept_recommended["branches"] == [[]]
    assert git("branch", "--list", "abc-123-fix-login", cwd=repo) != ""


def test_outside_repo_fails_cleanly(tmp_path, monkeypatch):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert run_cli() == 1
