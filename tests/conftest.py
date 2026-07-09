"""Shared fixtures: a real origin (bare repo) + clone with a mix of branches."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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
    subprocess.run(
        ["git", "clone", str(origin), str(clone)], capture_output=True, check=True
    )
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
