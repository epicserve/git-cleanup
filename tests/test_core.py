from pathlib import Path

from git_cleanup import gitops
from git_cleanup.config import Config
from git_cleanup.core import scan_repo
from git_cleanup.gitops import GitError
from git_cleanup.models import IssueInfo, IssueState
from tests.conftest import ME, git


def make_config() -> Config:
    return Config(provider="none")


def test_scan_repo(repo: Path, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    scan = scan_repo(make_config())

    assert scan.default_branch == "main"
    assert scan.current_branch == "main"
    assert scan.user_email == ME
    assert scan.issues_found == 0  # provider none

    names = {b.name for b in scan.branches}
    assert {"main", "abc-123-fix-login", "old-experiment", "abc-99-hotfix"} <= names

    # no UI output — reusable in CI
    assert capsys.readouterr().out == ""


def test_scan_repo_no_fetch_skips_prune(repo: Path, monkeypatch):
    monkeypatch.chdir(repo)
    # delete a branch directly on the bare origin (git push --delete would also
    # drop our remote-tracking ref); without fetch, the stale remote ref remains
    git("--git-dir", str(repo.parent / "origin.git"), "branch", "-D", "xyz-7-done-work", cwd=repo)
    scan = scan_repo(make_config(), fetch=False)
    remote = {b.name for b in scan.branches if b.has_remote}
    assert "xyz-7-done-work" in remote

    scan = scan_repo(make_config(), fetch=True)
    remote = {b.name for b in scan.branches if b.has_remote}
    assert "xyz-7-done-work" not in remote


def test_scan_repo_explicit_cwd(repo: Path):
    scan = scan_repo(make_config(), fetch=False, cwd=repo)
    assert scan.default_branch == "main"


def test_scan_repo_worktrees(repo_with_worktrees: Path, capsys):
    scan = scan_repo(make_config(), cwd=repo_with_worktrees)

    assert len(scan.worktrees) == 5
    assert scan.worktrees[0].is_main and scan.worktrees[0].is_current
    found = {wt.path.name: wt for wt in scan.worktrees}
    assert found["wt-dirty"].dirty_count == 1 and found["wt-dirty"].is_dirty
    assert found["wt-gone"].prunable and found["wt-gone"].dirty_count is None
    assert found["wt-locked"].locked and not found["wt-locked"].removable
    assert found["wt-merged"].short_branch == "abc-123-fix-login"
    assert found["wt-merged"].merged

    # still no UI output — reusable in CI
    assert capsys.readouterr().out == ""


def test_scan_repo_back_fills_branch_worktree_path(repo_with_worktrees: Path):
    scan = scan_repo(make_config(), cwd=repo_with_worktrees)
    by_name = {b.name: b for b in scan.branches}
    assert by_name["abc-123-fix-login"].has_worktree
    assert by_name["old-experiment"].worktree_path is None


def test_worktree_join_is_by_reference(repo_with_worktrees: Path):
    """Mutating a branch's issue after the scan flips WorktreeInfo.issue_done,
    proving the join stores a reference — so the block's position in scan_repo is
    a readability choice, not a correctness one."""
    scan = scan_repo(make_config(), cwd=repo_with_worktrees)
    worktree = next(wt for wt in scan.worktrees if wt.path.name == "wt-locked")
    assert not worktree.issue_done

    assert worktree.branch_info is not None
    worktree.branch_info.issue = IssueInfo(
        "XYZ-7", "done work", "Done", IssueState.DONE, "https://x/XYZ-7"
    )
    assert worktree.issue_done


def test_scan_repo_degrades_when_worktree_listing_fails(repo: Path, monkeypatch, capsys):
    def boom(cwd=None):
        raise GitError("worktree list exploded")

    monkeypatch.setattr(gitops, "list_worktrees", boom)
    scan = scan_repo(make_config(), cwd=repo)

    assert scan.worktrees == []
    assert scan.branches  # branch data still there
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not list worktrees" in captured.err


def test_scan_repo_stashes(repo_with_stashes: Path, capsys):
    scan = scan_repo(make_config(), cwd=repo_with_stashes)

    assert [s.selector for s in scan.stashes] == [f"stash@{{{i}}}" for i in range(4)]
    assert scan.stashes[0].message == "fix: login: retry"
    assert scan.stashes[1].wip and scan.stashes[1].branch == "abc-201-new-dashboard"
    assert scan.stashes[2].has_untracked and scan.stashes[2].file_count == 2
    assert scan.stashes[3].branch is None  # detached
    assert capsys.readouterr().out == ""


def test_scan_repo_no_stashes(repo: Path):
    assert scan_repo(make_config(), cwd=repo).stashes == []


def test_scan_repo_degrades_when_stash_listing_fails(repo: Path, monkeypatch, capsys):
    def boom(cwd=None):
        raise GitError("stash list exploded")

    monkeypatch.setattr(gitops, "list_stashes", boom)
    scan = scan_repo(make_config(), cwd=repo)

    assert scan.stashes == []
    assert scan.branches  # branch data still there
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not list stashes" in captured.err
