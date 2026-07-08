from pathlib import Path

import pytest

from git_cleanup import cli, ui
from tests.conftest import git


@pytest.fixture
def no_tracker_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text('[tracker]\nprovider = "none"\n')
    return path


@pytest.fixture
def answer_prompts(monkeypatch):
    """Select everything offered, confirm everything, record the prompts."""
    seen: dict[str, list] = {"selections": [], "confirms": []}

    def fake_select(branches, message, preselect):
        seen["selections"].append((message, [b.name for b in branches], preselect))
        return list(branches)

    def fake_confirm(message, default=False):
        seen["confirms"].append(message)
        return True

    monkeypatch.setattr(ui, "select_branches", fake_select)
    monkeypatch.setattr(ui, "confirm", fake_confirm)
    return seen


def run_cli(*argv: str) -> int:
    args = cli.build_parser().parse_args(list(argv))
    return cli.run(args)


def test_full_run_deletes_and_archives(repo, no_tracker_config, answer_prompts, monkeypatch):
    monkeypatch.chdir(repo)
    code = run_cli("--config", str(no_tracker_config))
    assert code == 0

    # Group A: my merged local branch deleted
    assert git("branch", "--list", "abc-123-fix-login", cwd=repo) == ""
    # Group B: same branch deleted on origin (extra confirm was shown)
    assert git("ls-remote", "--heads", "origin", "abc-123-fix-login", cwd=repo) == ""
    assert any("origin" in m for m in answer_prompts["confirms"])
    # Group C: old local-only branch archived (tag created, branch gone)
    assert "archive/old-experiment" in git("tag", "--list", "archive/*", cwd=repo)
    assert git("branch", "--list", "old-experiment", cwd=repo) == ""

    # Untouched: default branch, unmerged recent work, other author's branch
    assert git("branch", "--list", "main", cwd=repo) != ""
    assert git("branch", "--list", "abc-201-new-dashboard", cwd=repo) != ""
    assert git("ls-remote", "--heads", "origin", "abc-99-hotfix", cwd=repo) != ""


def test_dry_run_changes_nothing(repo, no_tracker_config, answer_prompts, monkeypatch):
    monkeypatch.chdir(repo)
    code = run_cli("--dry-run", "--config", str(no_tracker_config))
    assert code == 0

    assert git("branch", "--list", "abc-123-fix-login", cwd=repo) != ""
    assert git("ls-remote", "--heads", "origin", "abc-123-fix-login", cwd=repo) != ""
    assert git("branch", "--list", "old-experiment", cwd=repo) != ""
    assert git("tag", "--list", "archive/*", cwd=repo) == ""


def test_all_flag_includes_other_authors_remote(repo, no_tracker_config, answer_prompts, monkeypatch):
    monkeypatch.chdir(repo)
    code = run_cli("--all", "--config", str(no_tracker_config))
    assert code == 0
    # sarah's merged remote-only branch is offered and deleted with --all
    assert git("ls-remote", "--heads", "origin", "abc-99-hotfix", cwd=repo) == ""


def test_unselecting_everything_deletes_nothing(repo, no_tracker_config, monkeypatch):
    monkeypatch.chdir(repo)
    monkeypatch.setattr(ui, "select_branches", lambda branches, message, preselect: [])
    monkeypatch.setattr(ui, "confirm", lambda message, default=False: False)
    code = run_cli("--config", str(no_tracker_config))
    assert code == 0
    assert git("branch", "--list", "abc-123-fix-login", cwd=repo) != ""
    assert git("ls-remote", "--heads", "origin", "abc-123-fix-login", cwd=repo) != ""


def test_invalid_sort_fails_fast(repo, no_tracker_config, monkeypatch):
    monkeypatch.chdir(repo)
    assert run_cli("--sort", "bogus", "--config", str(no_tracker_config)) == 2


def test_sort_flag_accepted(repo, no_tracker_config, answer_prompts, monkeypatch):
    monkeypatch.chdir(repo)
    # descending specs must use the = form: --sort=-age (argparse reads a bare
    # "-age" token as a flag)
    assert run_cli("--dry-run", "--sort=-age,author", "--config", str(no_tracker_config)) == 0


def test_invalid_filter_fails_fast(repo, no_tracker_config, monkeypatch):
    monkeypatch.chdir(repo)
    assert run_cli("--filter", "age>abc", "--config", str(no_tracker_config)) == 2


def test_filter_restricts_cleanup_groups(repo, no_tracker_config, answer_prompts, monkeypatch):
    monkeypatch.chdir(repo)
    # filter matches nothing deletable, so select-all prompts get empty groups
    code = run_cli("--filter", "author=nobody", "--config", str(no_tracker_config))
    assert code == 0
    assert git("branch", "--list", "abc-123-fix-login", cwd=repo) != ""
    assert git("ls-remote", "--heads", "origin", "abc-123-fix-login", cwd=repo) != ""


def test_outside_repo_fails_cleanly(tmp_path, monkeypatch):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert run_cli() == 1
