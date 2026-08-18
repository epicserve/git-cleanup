from datetime import UTC, datetime, timedelta
from pathlib import Path

from git_cleanup.models import (
    RESTORE_ACTIONS,
    Action,
    BranchInfo,
    IssueInfo,
    IssueState,
    Outcome,
    StashAction,
    StashInfo,
    WorktreeAction,
    WorktreeInfo,
)


def make_branch(**overrides) -> BranchInfo:
    defaults = {
        "name": "abc-1-thing",
        "has_local": True,
        "has_remote": False,
        "sha": "deadbeef",
        "author_name": "Brent",
        "author_email": "brent@example.com",
        "committed_at": datetime.now(UTC) - timedelta(days=10),
        "merged": False,
    }
    defaults.update(overrides)
    return BranchInfo(**defaults)


def make_issue(state: IssueState = IssueState.DONE) -> IssueInfo:
    return IssueInfo(
        key="ABC-1", summary="Thing", status="Done", state=state, url="https://x/browse/ABC-1"
    )


def test_age_days():
    branch = make_branch(committed_at=datetime.now(UTC) - timedelta(days=42, hours=3))
    assert branch.age_days == 42


def test_is_mine_case_insensitive():
    branch = make_branch(author_email="Brent@Example.com")
    assert branch.is_mine("brent@example.com")
    assert not branch.is_mine("sarah@example.com")
    assert not branch.is_mine("")


def test_issue_done():
    assert make_branch(issue=make_issue()).issue_done
    assert not make_branch(issue=make_issue(IssueState.OPEN)).issue_done
    assert not make_branch(issue=None).issue_done


def test_cleanup_eligible_merged():
    assert make_branch(merged=True).cleanup_eligible


def test_cleanup_eligible_issue_done():
    assert make_branch(merged=False, issue=make_issue()).cleanup_eligible


def test_cleanup_never_for_current_default_protected():
    for kwargs in ({"is_current": True}, {"is_default": True}, {"is_protected": True}):
        branch = make_branch(merged=True, issue=make_issue(), **kwargs)
        assert not branch.cleanup_eligible, kwargs


def test_not_eligible_when_unmerged_and_open():
    assert not make_branch(merged=False, issue=make_issue(IssueState.OPEN)).cleanup_eligible


def make_worktree(**overrides) -> WorktreeInfo:
    defaults = {
        "path": Path("/home/x/wt/thing"),
        "head": "deadbeefcafe",
        "branch": "refs/heads/feat",
    }
    defaults.update(overrides)
    return WorktreeInfo(**defaults)


def test_worktree_name_is_the_path_string():
    assert make_worktree(path=Path("/a/b")).name == "/a/b"


def test_worktree_short_branch():
    assert make_worktree().short_branch == "feat"
    assert make_worktree(branch=None).short_branch is None


def test_worktree_needs_force_tracks_dirty_count():
    assert not make_worktree(dirty_count=0).needs_force
    assert not make_worktree(dirty_count=None).needs_force  # unknown is not "dirty"
    assert make_worktree(dirty_count=3).needs_force
    assert make_worktree(dirty_count=3).is_dirty


def test_worktree_removable_false_for_main_current_and_locked():
    assert make_worktree().removable
    assert not make_worktree(is_main=True).removable
    assert not make_worktree(is_current=True).removable
    assert not make_worktree(locked=True).removable


def test_worktree_is_missing_tracks_prunable():
    assert make_worktree(prunable=True).is_missing
    assert not make_worktree().is_missing


def test_worktree_delegates_to_branch_info():
    branch = make_branch(merged=True, issue=make_issue())
    worktree = make_worktree(branch_info=branch)
    assert worktree.merged and worktree.issue_done
    assert worktree.age_days == branch.age_days
    assert worktree.is_mine("brent@example.com")

    orphan = make_worktree(branch_info=None)
    assert orphan.age_days is None
    assert not orphan.merged and not orphan.issue_done
    assert not orphan.is_mine("brent@example.com")


def test_branch_has_worktree():
    assert not make_branch().has_worktree
    assert make_branch(worktree_path=Path("/a/b")).has_worktree


def test_outcome_truthiness():
    assert not Outcome()
    assert Outcome(branches=[(make_branch(), Action.DELETE)])
    assert Outcome(worktrees=[(make_worktree(), WorktreeAction.REMOVE)])


def make_stash(**overrides) -> StashInfo:
    defaults = {
        "index": 0,
        "selector": "stash@{0}",
        "sha": "deadbeefcafe",
        "created_at": datetime.now(UTC) - timedelta(days=4),
        "subject": "On main: thing",
        "branch": "main",
        "message": "thing",
        "wip": False,
        "parent_count": 2,
    }
    defaults.update(overrides)
    return StashInfo(**defaults)


def test_stash_name_is_the_selector_not_the_sha():
    """A sha is not unique (git stash store twice); a reflog position is unique
    within one scan."""
    assert make_stash(selector="stash@{3}").name == "stash@{3}"


def test_stash_has_untracked_from_parent_count():
    assert not make_stash(parent_count=2).has_untracked
    assert make_stash(parent_count=3).has_untracked


def test_stash_age_days():
    assert make_stash(created_at=datetime.now(UTC) - timedelta(days=9, hours=2)).age_days == 9


def test_restore_actions_are_the_working_tree_ones():
    assert RESTORE_ACTIONS == {StashAction.POP, StashAction.APPLY}
    assert StashAction.DROP not in RESTORE_ACTIONS


def test_apply_is_the_longest_stash_action_label():
    """The stash Action column is sized with len(StashAction.APPLY) up front,
    because cell updates pass update_width=False."""
    assert max(len(a.value) for a in StashAction) == len(StashAction.APPLY) == 5


def test_outcome_includes_stashes():
    assert Outcome().stashes == []
    assert Outcome(stashes=[(make_stash(), StashAction.DROP)])
    # stashes is appended last, so positional construction still works
    assert not Outcome([], [])
