from datetime import UTC, datetime, timedelta

from git_cleanup.models import BranchInfo, IssueInfo, IssueState


def make_branch(**overrides) -> BranchInfo:
    defaults = dict(
        name="abc-1-thing",
        has_local=True,
        has_remote=False,
        sha="deadbeef",
        author_name="Brent",
        author_email="brent@example.com",
        committed_at=datetime.now(UTC) - timedelta(days=10),
        merged=False,
    )
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
