from pathlib import Path

import pytest

from git_cleanup import gitops
from tests.conftest import ME, OTHER, git


def test_in_git_repo(repo: Path, tmp_path: Path):
    assert gitops.in_git_repo(cwd=repo)
    outside = tmp_path / "empty"
    outside.mkdir()
    assert not gitops.in_git_repo(cwd=outside)


def test_has_origin(repo: Path):
    assert gitops.has_origin(cwd=repo)


def test_get_default_branch(repo: Path):
    assert gitops.get_default_branch(cwd=repo) == "main"


def test_origin_web_url_path_remote_is_none(repo: Path):
    # the fixture's origin is a local filesystem path
    assert gitops.origin_web_url(cwd=repo) is None


@pytest.mark.parametrize(
    "raw",
    [
        "git@github.com:acme/widgets.git",
        "ssh://git@github.com/acme/widgets.git",
        "https://github.com/acme/widgets.git",
        "https://github.com/acme/widgets",
    ],
)
def test_origin_web_url_normalizes_remote_forms(repo: Path, raw: str):
    git("remote", "set-url", "origin", raw, cwd=repo)
    assert gitops.origin_web_url(cwd=repo) == "https://github.com/acme/widgets"


def test_compare_url_quotes_branch_but_keeps_slashes():
    url = gitops.compare_url("https://github.com/acme/widgets", "main", "feat/log#2")
    assert url == "https://github.com/acme/widgets/compare/main...feat/log%232"


def test_get_current_branch(repo: Path):
    assert gitops.get_current_branch(cwd=repo) == "main"


def test_get_user_email(repo: Path):
    assert gitops.get_user_email(cwd=repo) == ME


def test_list_refs_merges_local_and_remote(repo: Path):
    refs = gitops.list_refs(cwd=repo)
    names = {r.short_name for r in refs}
    assert "abc-123-fix-login" in names
    assert "old-experiment" in names  # local only
    assert "abc-99-hotfix" in names  # remote only
    assert "HEAD" not in names

    local = {r.short_name for r in refs if not r.is_remote}
    remote = {r.short_name for r in refs if r.is_remote}
    assert "old-experiment" in local and "old-experiment" not in remote
    assert "abc-99-hotfix" in remote and "abc-99-hotfix" not in local

    hotfix = next(r for r in refs if r.short_name == "abc-99-hotfix")
    assert hotfix.author_email == OTHER


def test_list_refs_ahead_behind(repo: Path):
    refs = {r.refname: r for r in gitops.list_refs(cwd=repo)}

    # one unpushed commit on abc-201
    dash = refs["refs/heads/abc-201-new-dashboard"]
    assert (dash.ahead, dash.behind, dash.upstream_gone) == (1, 0, False)

    # in sync with upstream
    login = refs["refs/heads/abc-123-fix-login"]
    assert (login.ahead, login.behind) == (0, 0)

    # never pushed: no upstream
    experiment = refs["refs/heads/old-experiment"]
    assert experiment.ahead is None and not experiment.upstream_gone

    # remote refs have no upstream tracking
    remote = refs["refs/remotes/origin/abc-99-hotfix"]
    assert remote.ahead is None


def test_list_refs_upstream_gone(repo: Path):
    git("push", "origin", "--delete", "xyz-7-done-work", cwd=repo)
    gitops.fetch_prune(cwd=repo)
    refs = {r.refname: r for r in gitops.list_refs(cwd=repo)}
    assert refs["refs/heads/xyz-7-done-work"].upstream_gone


def test_merged_ref_names(repo: Path):
    merged = gitops.merged_ref_names("main", cwd=repo)
    assert "refs/heads/abc-123-fix-login" in merged
    assert "refs/remotes/origin/abc-99-hotfix" in merged
    assert "refs/heads/abc-201-new-dashboard" not in merged
    assert "refs/heads/old-experiment" not in merged


def test_delete_local_branch(repo: Path):
    gitops.delete_local_branch("abc-123-fix-login", cwd=repo)
    branches = git("branch", "--list", "abc-123-fix-login", cwd=repo)
    assert branches == ""


def test_delete_local_unmerged_requires_force(repo: Path):
    # old-experiment has no upstream and is not merged into HEAD, so -d refuses
    with pytest.raises(gitops.GitError):
        gitops.delete_local_branch("old-experiment", cwd=repo)
    gitops.delete_local_branch("old-experiment", force=True, cwd=repo)


def test_delete_remote_branch(repo: Path):
    gitops.delete_remote_branch("abc-99-hotfix", cwd=repo)
    out = git("ls-remote", "--heads", "origin", "abc-99-hotfix", cwd=repo)
    assert out == ""


def test_tag_create_push_exists(repo: Path):
    sha = git("rev-parse", "old-experiment", cwd=repo)
    tag = "archive/old-experiment"
    assert not gitops.tag_exists(tag, cwd=repo)
    gitops.create_tag(tag, sha, cwd=repo)
    assert gitops.tag_exists(tag, cwd=repo)
    gitops.push_tag(tag, cwd=repo)
    out = git("ls-remote", "--tags", "origin", tag, cwd=repo)
    assert tag in out


def test_fetch_prune_removes_gone_remote(repo: Path):
    git("push", "origin", "--delete", "xyz-7-done-work", cwd=repo)
    gitops.fetch_prune(cwd=repo)
    refs = gitops.list_refs(cwd=repo)
    remote = {r.short_name for r in refs if r.is_remote}
    assert "xyz-7-done-work" not in remote
