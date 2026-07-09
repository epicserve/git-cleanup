from pathlib import Path

from git_cleanup.config import Config
from git_cleanup.core import scan_repo
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
