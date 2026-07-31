from pathlib import Path

import pytest

from git_cleanup import gitops
from tests.conftest import LOCK_REASON, ME, OTHER, git


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


def test_parse_worktree_records_nul_form():
    out = "worktree /a\0HEAD aaa\0branch refs/heads/main\0\0worktree /b\0HEAD bbb\0detached\0\0"
    first, second = gitops._parse_worktree_records(out, "\0")
    assert (str(first.path), first.short_branch, first.head) == ("/a", "main", "aaa")
    assert second.detached and second.branch is None and second.short_branch is None


def test_parse_worktree_records_last_record_survives_strip():
    """run_git ends with .strip(), which eats the newline form's trailing blank
    line — a parser that only flushes on an empty attribute drops the last
    worktree. '\\0'.isspace() is False, so the -z form keeps its terminators."""
    raw = "worktree /a\nHEAD aaa\nbranch refs/heads/main\n\nworktree /b\nHEAD bbb\ndetached\n\n"
    records = gitops._parse_worktree_records(raw.strip(), "\n")
    assert [str(w.path) for w in records] == ["/a", "/b"]

    nul = "worktree /a\0HEAD aaa\0branch refs/heads/main\0\0"
    assert len(gitops._parse_worktree_records(nul, "\0")) == 1  # no phantom record


def test_parse_worktree_records_locked_without_reason():
    out = "worktree /a\0HEAD aaa\0branch refs/heads/x\0locked\0\0"
    (wt,) = gitops._parse_worktree_records(out, "\0")
    assert wt.locked and wt.lock_reason == ""


def test_parse_worktree_records_bare_and_prunable():
    out = "worktree /a\0bare\0\0worktree /b\0HEAD bbb\0detached\0prunable gitdir file is broken\0\0"
    bare, broken = gitops._parse_worktree_records(out, "\0")
    assert bare.bare and bare.head is None
    assert broken.prunable and broken.prune_reason == "gitdir file is broken"


def test_parse_worktree_records_ignores_unknown_labels():
    out = "worktree /a\0HEAD aaa\0branch refs/heads/x\0futurething value\0\0"
    (wt,) = gitops._parse_worktree_records(out, "\0")
    assert wt.short_branch == "x"


def _by_dir(repo: Path) -> dict[str, gitops.RawWorktree]:
    return {w.path.name: w for w in gitops.list_worktrees(cwd=repo)}


def test_list_worktrees_main_first_with_flags(repo_with_worktrees: Path):
    worktrees = gitops.list_worktrees(cwd=repo_with_worktrees)
    assert len(worktrees) == 5
    # git documents the main worktree as listed first
    assert worktrees[0].path.name == repo_with_worktrees.name
    assert worktrees[0].short_branch == "main"

    found = {w.path.name: w for w in worktrees}
    assert found["wt-merged"].short_branch == "abc-123-fix-login"
    assert found["wt-locked"].locked
    assert found["wt-gone"].prunable and found["wt-gone"].detached


def test_list_worktrees_lock_reason_round_trips_verbatim(repo_with_worktrees: Path):
    """-z keeps reasons byte-for-byte; without it git C-quotes them per
    core.quotePath, so this fails if someone drops -z."""
    assert _by_dir(repo_with_worktrees)["wt-locked"].lock_reason == LOCK_REASON
    assert '"' in LOCK_REASON and "  " in LOCK_REASON  # the parts that get mangled


def test_worktree_dirty_count(repo_with_worktrees: Path):
    outside = repo_with_worktrees.parent
    assert gitops.worktree_dirty_count(outside / "wt-merged") == 0
    assert gitops.worktree_dirty_count(outside / "wt-dirty") == 1
    # the directory is gone: subprocess's cwd= would raise FileNotFoundError,
    # which is not a GitError; `git -C` exits 128 instead so this returns None
    assert gitops.worktree_dirty_count(outside / "wt-gone") is None


def test_worktree_dirty_count_counts_tracked_and_untracked(repo_with_worktrees: Path):
    worktree = repo_with_worktrees.parent / "wt-merged"
    (worktree / "login.txt").write_text("modified")  # tracked
    (worktree / "brand-new.txt").write_text("new")  # untracked
    assert gitops.worktree_dirty_count(worktree) == 2


def test_remove_worktree_leaves_the_branch_alive(repo_with_worktrees: Path):
    worktree = repo_with_worktrees.parent / "wt-merged"
    gitops.remove_worktree(worktree, cwd=repo_with_worktrees)
    assert not worktree.exists()
    assert "wt-merged" not in _by_dir(repo_with_worktrees)
    assert git("branch", "--list", "abc-123-fix-login", cwd=repo_with_worktrees) != ""


def test_remove_worktree_dirty_requires_force(repo_with_worktrees: Path):
    worktree = repo_with_worktrees.parent / "wt-dirty"
    with pytest.raises(gitops.GitError):
        gitops.remove_worktree(worktree, cwd=repo_with_worktrees)
    gitops.remove_worktree(worktree, force=True, cwd=repo_with_worktrees)
    assert not worktree.exists()


def test_remove_worktree_locked_fails_even_with_force(repo_with_worktrees: Path):
    """Documents why the executor refuses locked worktrees rather than
    force-removing them: that would need -f -f, which we never pass."""
    worktree = repo_with_worktrees.parent / "wt-locked"
    with pytest.raises(gitops.GitError):
        gitops.remove_worktree(worktree, force=True, cwd=repo_with_worktrees)
    assert worktree.exists()


def test_remove_worktree_refuses_main(repo_with_worktrees: Path):
    with pytest.raises(gitops.GitError):
        gitops.remove_worktree(repo_with_worktrees, force=True, cwd=repo_with_worktrees)
    assert (repo_with_worktrees / "base.txt").exists()


def test_prune_worktrees_dry_run_then_real(repo_with_worktrees: Path):
    report = gitops.prune_worktrees(dry_run=True, cwd=repo_with_worktrees)
    assert any("wt-gone" in line for line in report)
    assert "wt-gone" in _by_dir(repo_with_worktrees)  # dry run changed nothing

    pruned = gitops.prune_worktrees(cwd=repo_with_worktrees)
    assert any("wt-gone" in line for line in pruned)
    remaining = _by_dir(repo_with_worktrees)
    assert "wt-gone" not in remaining
    assert "wt-locked" in remaining  # prune skips locked worktrees


def test_delete_local_branch_blocked_until_worktree_removed(repo_with_worktrees: Path):
    """The git fact the whole execution ordering rests on: `git branch -d/-D`
    refuses a branch that is checked out in any worktree."""
    with pytest.raises(gitops.GitError):
        gitops.delete_local_branch("abc-123-fix-login", force=True, cwd=repo_with_worktrees)

    gitops.remove_worktree(repo_with_worktrees.parent / "wt-merged", cwd=repo_with_worktrees)
    gitops.delete_local_branch("abc-123-fix-login", force=True, cwd=repo_with_worktrees)
    assert git("branch", "--list", "abc-123-fix-login", cwd=repo_with_worktrees) == ""


def test_fetch_prune_removes_gone_remote(repo: Path):
    git("push", "origin", "--delete", "xyz-7-done-work", cwd=repo)
    gitops.fetch_prune(cwd=repo)
    refs = gitops.list_refs(cwd=repo)
    remote = {r.short_name for r in refs if r.is_remote}
    assert "xyz-7-done-work" not in remote
