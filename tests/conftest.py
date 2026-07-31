"""Shared fixtures: a real origin (bare repo) + clone with a mix of branches."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from git_cleanup.gitops import RawStash, RawWorktree

ME = "brent@example.com"
OTHER = "sarah@example.com"
OLD_DATE = "2024-01-15T12:00:00"


def git(*args: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    import os

    full_env = os.environ.copy()
    full_env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_AUTHOR_EMAIL": ME,
            "GIT_COMMITTER_EMAIL": ME,
        }
    )
    if env:
        full_env.update(env)
    result = subprocess.run(
        # never sign in tests — the user's signing agent (e.g. 1Password) may
        # prompt or be locked, hanging or failing the suite
        ["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=full_env,
    )
    assert result.returncode == 0, f"git {' '.join(args)}: {result.stderr}"
    return result.stdout.strip()


def commit(repo: Path, filename: str, *, email: str = ME, date: str | None = None) -> None:
    (repo / filename).write_text(filename)
    git("add", ".", cwd=repo)
    env = {"GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email}
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    git("commit", "-m", f"add {filename}", cwd=repo, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clone of a bare 'origin' with a representative set of branches.

    Branches:
      main                      - default branch
      abc-123-fix-login         - mine, local+remote, MERGED into main
      abc-201-new-dashboard     - mine, local+remote, unmerged, 1 unpushed commit
      old-experiment            - mine, local only (no upstream), unmerged, very old
      abc-99-hotfix             - other author, remote only, MERGED
      xyz-7-done-work           - mine, local+remote, unmerged (issue will be Done)
    """
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git("init", "--bare", "--initial-branch=main", cwd=origin)

    clone = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(clone)], capture_output=True, check=True)
    git("config", "user.email", ME, cwd=clone)
    git("config", "user.name", "Brent", cwd=clone)
    git("checkout", "-b", "main", cwd=clone)
    commit(clone, "base.txt")
    git("push", "-u", "origin", "main", cwd=clone)

    # merged branch (mine)
    git("checkout", "-b", "abc-123-fix-login", cwd=clone)
    commit(clone, "login.txt")
    git("push", "-u", "origin", "abc-123-fix-login", cwd=clone)
    git("checkout", "main", cwd=clone)
    git("merge", "--no-ff", "abc-123-fix-login", "-m", "merge login fix", cwd=clone)
    git("push", "origin", "main", cwd=clone)

    # unmerged recent branch (mine), one commit ahead of its upstream
    git("checkout", "-b", "abc-201-new-dashboard", cwd=clone)
    commit(clone, "dash.txt")
    git("push", "-u", "origin", "abc-201-new-dashboard", cwd=clone)
    commit(clone, "dash2.txt")
    git("checkout", "main", cwd=clone)

    # old local-only branch (mine)
    git("checkout", "-b", "old-experiment", cwd=clone)
    commit(clone, "exp.txt", date=OLD_DATE)
    git("checkout", "main", cwd=clone)

    # merged remote-only branch by another author
    git("checkout", "-b", "abc-99-hotfix", cwd=clone)
    commit(clone, "hotfix.txt", email=OTHER)
    git("checkout", "main", cwd=clone)
    git("merge", "--no-ff", "abc-99-hotfix", "-m", "merge hotfix", cwd=clone)
    git("push", "origin", "main", cwd=clone)
    git("push", "origin", "abc-99-hotfix", cwd=clone)
    git("branch", "-D", "abc-99-hotfix", cwd=clone)

    # unmerged branch whose issue will be Done in Jira (mine)
    git("checkout", "-b", "xyz-7-done-work", cwd=clone)
    commit(clone, "done.txt")
    git("push", "-u", "origin", "xyz-7-done-work", cwd=clone)
    git("checkout", "main", cwd=clone)

    # make origin/HEAD known so get_default_branch works via symbolic-ref
    git("remote", "set-head", "origin", "main", cwd=clone)
    git("fetch", "--prune", "origin", cwd=clone)
    return clone


LOCK_REASON = 'on a  "network" share'


@pytest.fixture
def repo_with_worktrees(repo: Path) -> Path:
    """`repo` plus four linked worktrees covering every state.

      wt-merged  abc-123-fix-login      merged, clean      -> pre-marked
      wt-dirty   abc-201-new-dashboard  unmerged, 1 untracked file
      wt-locked  xyz-7-done-work        locked
      wt-gone    detached at main       directory deleted  -> prunable

    The worktrees live *outside* the repo directory: a nested worktree shows up
    as untracked in the main worktree and would be swallowed by commit()'s
    `git add .`. None of the three branches is checked out by `repo` itself, so
    none hits "already checked out".
    """
    outside = repo.parent

    git("worktree", "add", str(outside / "wt-merged"), "abc-123-fix-login", cwd=repo)

    git("worktree", "add", str(outside / "wt-dirty"), "abc-201-new-dashboard", cwd=repo)
    (outside / "wt-dirty" / "scratch.txt").write_text("uncommitted work")

    git("worktree", "add", str(outside / "wt-locked"), "xyz-7-done-work", cwd=repo)
    git("worktree", "lock", "--reason", LOCK_REASON, str(outside / "wt-locked"), cwd=repo)

    # --detach because main is already checked out in `repo` itself
    git("worktree", "add", "--detach", str(outside / "wt-gone"), "main", cwd=repo)
    shutil.rmtree(outside / "wt-gone")

    return repo


def raw_worktree(path: str, branch: str | None = None, **flags) -> RawWorktree:
    """Convenience builder for the pure (no-git) parser and planner tests."""
    return RawWorktree(
        path=Path(path),
        head=flags.pop("head", "0" * 40),
        branch=f"refs/heads/{branch}" if branch else None,
        **flags,
    )


@pytest.fixture
def repo_with_stashes(repo: Path) -> Path:
    """`repo` plus four stashes covering every subject shape the parser handles.

    Created newest-last, so reflog order (stash@{0} is the NEWEST) ends up:

      stash@{0}  On main: fix: login: retry             a message containing ': '
      stash@{1}  WIP on abc-201-new-dashboard: <sha> …  no -m, so git's WIP subject
      stash@{2}  On main: with untracked                -u, so 3 parents
      stash@{3}  On (no branch): detached               made on a detached HEAD

    INVARIANT: this leaves the working tree CLEAN. `git stash pop` refuses
    outright when a tracked file it would restore is dirty, so every pop test
    depends on it. The `git stash store` shape (a subject with no "On" prefix) is
    deliberately absent — store leaves the tree dirty — and is covered by the
    pure parse_stash_subject test instead.
    """
    # 3: detached HEAD -> "On (no branch): ...". base.txt exists in every commit,
    # so editing it needs no `git add`.
    git("checkout", "--detach", "main", cwd=repo)
    (repo / "base.txt").write_text("detached edit")
    git("stash", "push", "-m", "detached", cwd=repo)
    git("checkout", "main", cwd=repo)

    # 2: -u -> 3 parents, with the untracked file captured in the stash
    (repo / "base.txt").write_text("edit with untracked")
    (repo / "extra.txt").write_text("untracked")
    git("stash", "push", "-u", "-m", "with untracked", cwd=repo)

    # 1: no -m on another branch -> "WIP on abc-201-new-dashboard: <sha> <subj>"
    git("checkout", "abc-201-new-dashboard", cwd=repo)
    (repo / "dash.txt").write_text("wip edit")
    git("stash", cwd=repo)
    git("checkout", "main", cwd=repo)

    # 0: a message that itself contains ': '
    (repo / "base.txt").write_text("named edit")
    git("stash", "push", "-m", "fix: login: retry", cwd=repo)
    return repo


def raw_stash(index: int, subject: str, **flags) -> RawStash:
    """Convenience builder for the pure (no-git) parser and planner tests."""
    return RawStash(
        index=index,
        selector=flags.pop("selector", f"stash@{{{index}}}"),
        sha=flags.pop("sha", f"{index:040d}"),
        created_at=flags.pop("created_at", datetime(2026, 7, 1, tzinfo=UTC)),
        parents=flags.pop("parents", ("p1", "p2")),
        subject=subject,
    )
