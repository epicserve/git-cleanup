from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from git_cleanup.models import BranchInfo, StashInfo, WorktreeInfo
from git_cleanup.ui import (
    _sync_text,
    _worktree_state_text,
    format_age,
    format_worktree_path,
    render_stash_table,
    render_worktree_table,
    stash_files_label,
    worktree_flags,
)


def make_branch(**overrides) -> BranchInfo:
    defaults = dict(
        name="abc-1-thing",
        has_local=True,
        has_remote=True,
        sha="deadbeef",
        author_name="Brent",
        author_email="brent@example.com",
        committed_at=datetime.now(UTC) - timedelta(days=12),
        merged=False,
        ahead=0,
        behind=0,
    )
    defaults.update(overrides)
    return BranchInfo(**defaults)


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, "0d"),
        (12, "12d"),
        (30, "1m"),
        (45, "1m 15d"),
        (364, "1y 4d"),
        (365, "1y"),
        (400, "1y 1m 5d"),
        (730, "2y"),
        (919, "2y 6m 9d"),
    ],
)
def test_format_age(days: int, expected: str):
    assert format_age(days) == expected


def test_sync_text():
    assert _sync_text(make_branch(ahead=1, behind=2)) == "↑1 ↓2"
    assert _sync_text(make_branch(ahead=3)) == "↑3"
    assert _sync_text(make_branch(behind=4)) == "↓4"
    assert _sync_text(make_branch()) == "✓"
    assert _sync_text(make_branch(ahead=None, behind=None)) == "—"  # no upstream
    assert _sync_text(make_branch(ahead=None, upstream_gone=True)) == "gone"
    assert _sync_text(make_branch(has_local=False, ahead=None)) == ""  # remote-only


def make_worktree(**overrides) -> WorktreeInfo:
    defaults = dict(path=Path("/home/x/wt/thing"), head="deadbeefcafe", branch="refs/heads/feat")
    defaults.update(overrides)
    return WorktreeInfo(**defaults)


def test_worktree_state_text_precedence():
    # a missing directory outranks a lock: the lock is moot once it is gone
    assert _worktree_state_text(make_worktree(prunable=True, locked=True)) == "missing"
    assert _worktree_state_text(make_worktree(locked=True, bare=True)) == "locked"
    assert _worktree_state_text(make_worktree(bare=True, is_main=True)) == "bare"
    assert _worktree_state_text(make_worktree(is_main=True, detached=True)) == "main"
    assert _worktree_state_text(make_worktree(detached=True)) == "detached"
    assert _worktree_state_text(make_worktree()) == ""


def test_worktree_flags_are_orthogonal():
    assert worktree_flags(make_worktree()) == []
    assert worktree_flags(make_worktree(is_main=True)) == ["main"]
    assert worktree_flags(make_worktree(dirty_count=3)) == ["dirty 3"]
    assert worktree_flags(make_worktree(dirty_count=0)) == []
    flags = worktree_flags(
        make_worktree(is_main=True, prunable=True, locked=True, dirty_count=2, detached=True)
    )
    assert flags == ["main", "missing", "locked", "dirty 2", "detached"]


def test_format_worktree_path_collapses_home(monkeypatch):
    monkeypatch.setenv("HOME", "/home/someone")
    assert format_worktree_path(Path("/home/someone/code/wt")) == "~/code/wt"
    assert format_worktree_path(Path("/home/someone")) == "~"
    assert format_worktree_path(Path("/var/tmp/wt")) == "/var/tmp/wt"


def test_render_worktree_table_smoke(capsys):
    """Covers the age_days is None and dirty_count is None branches."""
    branch = make_branch(name="feat", merged=True, issue_key="ABC-1")
    render_worktree_table(
        [
            make_worktree(path=Path("/repo"), is_main=True, is_current=True, dirty_count=0),
            make_worktree(branch_info=branch, dirty_count=2),
            make_worktree(path=Path("/wt/det"), branch=None, detached=True, dirty_count=None),
            make_worktree(path=Path("/wt/gone"), prunable=True, dirty_count=None),
            make_worktree(path=Path("/bare"), branch=None, bare=True),
        ]
    )
    out = capsys.readouterr().out
    assert "Worktrees" in out
    assert "missing" in out and "detached" in out


def make_stash(**overrides) -> StashInfo:
    defaults = dict(
        index=0,
        selector="stash@{0}",
        sha="deadbeefcafe",
        created_at=datetime.now(UTC) - timedelta(days=4),
        subject="On main: thing",
        branch="main",
        message="thing",
        wip=False,
        parent_count=2,
    )
    defaults.update(overrides)
    return StashInfo(**defaults)


def test_stash_files_label():
    assert stash_files_label(make_stash(file_count=None)) == "—"
    assert stash_files_label(make_stash(file_count=0)) == "0"
    assert stash_files_label(make_stash(file_count=5)) == "5"
    assert stash_files_label(make_stash(file_count=5, parent_count=3)) == "5 +u"


def test_render_stash_table_smoke(capsys):
    render_stash_table(
        [
            make_stash(file_count=2),
            make_stash(index=1, selector="stash@{1}", wip=True, message="abc1234 wip"),
            make_stash(index=2, selector="stash@{2}", branch=None, file_count=None),
        ]
    )
    out = capsys.readouterr().out
    assert "Stashes" in out
    assert "stash@{0}" in out and "detached" in out
