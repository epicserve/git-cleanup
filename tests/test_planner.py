from datetime import UTC, datetime, timedelta

import pytest

from git_cleanup import planner
from git_cleanup.gitops import RawRef
from git_cleanup.models import IssueInfo, IssueState

ME = "brent@example.com"
OTHER = "sarah@example.com"
NOW = datetime.now(UTC)


def ref(name: str, *, remote: bool = False, email: str = ME, days_old: int = 5) -> RawRef:
    prefix = "refs/remotes/origin/" if remote else "refs/heads/"
    return RawRef(
        refname=f"{prefix}{name}",
        sha=f"sha-{name}",
        author_email=email,
        author_name="X",
        committed_at=NOW - timedelta(days=days_old),
    )


def build(refs, merged_names, **kwargs):
    defaults = dict(current="main", default="main", protected=frozenset({"main"}))
    defaults.update(kwargs)
    return planner.build_branches(refs, merged_names, **defaults)


def test_build_copies_tracking_from_local_ref():
    from dataclasses import replace

    local = replace(ref("feat"), ahead=2, behind=1)
    branches = build([local, ref("feat", remote=True)], set())
    feat = next(b for b in branches if b.name == "feat")
    assert (feat.ahead, feat.behind) == (2, 1)
    assert feat.has_unpushed

    remote_only = next(b for b in build([ref("solo", remote=True)], set()) if b.name == "solo")
    assert remote_only.ahead is None and not remote_only.has_unpushed


def test_build_merges_local_and_remote():
    branches = build(
        [ref("feat"), ref("feat", remote=True), ref("remote-only", remote=True)],
        set(),
    )
    by_name = {b.name: b for b in branches}
    assert by_name["feat"].has_local and by_name["feat"].has_remote
    assert not by_name["remote-only"].has_local and by_name["remote-only"].has_remote


def test_merged_requires_both_sides():
    refs = [ref("feat"), ref("feat", remote=True)]
    feat = next(b for b in build(refs, {"refs/heads/feat"}) if b.name == "feat")
    assert not feat.merged  # remote side unmerged
    both = {"refs/heads/feat", "refs/remotes/origin/feat"}
    feat = next(b for b in build(refs, both) if b.name == "feat")
    assert feat.merged


def test_flags_current_default_protected():
    branches = build(
        [ref("main"), ref("develop")],
        set(),
        current="main",
        default="main",
        protected=frozenset({"main", "develop"}),
    )
    by_name = {b.name: b for b in branches}
    assert by_name["main"].is_current and by_name["main"].is_default
    assert by_name["develop"].is_protected


def make_branches():
    refs = [
        ref("main"),
        ref("main", remote=True),
        ref("abc-1-merged"),
        ref("abc-1-merged", remote=True),
        ref("abc-2-open"),
        ref("abc-2-open", remote=True),
        ref("abc-3-theirs", remote=True, email=OTHER),
        ref("old-thing", days_old=200),
    ]
    merged = {
        "refs/heads/main",
        "refs/remotes/origin/main",
        "refs/heads/abc-1-merged",
        "refs/remotes/origin/abc-1-merged",
        "refs/remotes/origin/abc-3-theirs",
    }
    return build(refs, merged)


def test_my_local_cleanup():
    branches = make_branches()
    assert [b.name for b in planner.my_local_cleanup(branches, ME)] == ["abc-1-merged"]


def test_remote_cleanup_mine_only_vs_all():
    branches = make_branches()
    assert [b.name for b in planner.remote_cleanup(branches, ME)] == ["abc-1-merged"]
    assert [b.name for b in planner.remote_cleanup(branches, ME, include_all=True)] == [
        "abc-1-merged",
        "abc-3-theirs",
    ]


def test_issue_done_makes_eligible():
    branches = make_branches()
    open_branch = next(b for b in branches if b.name == "abc-2-open")
    open_branch.issue_key = "ABC-2"
    open_branch.issue = IssueInfo("ABC-2", "x", "Done", IssueState.DONE, "u")
    assert [b.name for b in planner.my_local_cleanup(branches, ME)] == [
        "abc-1-merged",
        "abc-2-open",
    ]


def test_archive_candidates_excludes_selected_and_recent():
    branches = make_branches()
    selected = planner.my_local_cleanup(branches, ME)
    candidates = planner.archive_candidates(branches, selected, age_days=90)
    assert [b.name for b in candidates] == ["old-thing"]


def test_archive_candidates_never_protected():
    branches = make_branches()
    candidates = planner.archive_candidates(branches, [], age_days=0)
    names = {b.name for b in candidates}
    assert "main" not in names


def test_parse_sort():
    assert planner.parse_sort("-age,status,author") == [
        ("age", True),
        ("status", False),
        ("author", False),
    ]
    assert planner.parse_sort("name") == [("branch", False)]  # alias
    assert planner.parse_sort(" -Merged , branch ") == [("merged", True), ("branch", False)]


def test_parse_sort_rejects_unknown_column():
    with pytest.raises(ValueError, match="unknown sort column 'bogus'"):
        planner.parse_sort("age,bogus")


def test_sort_branches_multi_column():
    def branch(name, days_old, status=None):
        b = build([ref(name, days_old=days_old)], set())[0]
        if status:
            b.issue = IssueInfo("K-1", "x", status, IssueState.OPEN, "u")
        return b

    branches = [
        branch("a", days_old=5, status="Done"),
        branch("b", days_old=90, status="Open"),
        branch("c", days_old=90, status="Done"),
        branch("d", days_old=30),
    ]
    result = planner.sort_branches(branches, planner.parse_sort("-age,status"))
    # oldest first; equal ages tie-broken by status (no-status "" sorts first)
    assert [b.name for b in result] == ["c", "b", "d", "a"]

    result = planner.sort_branches(branches, planner.parse_sort("status,-branch"))
    assert [b.name for b in result] == ["d", "c", "a", "b"]


def test_parse_filter():
    assert planner.parse_filter("mine,!merged") == [("bool", "mine", True), ("bool", "merged", False)]
    assert planner.parse_filter("age>90") == [("age", ">", 90)]
    assert planner.parse_filter("age<=6m") == [("age", "<=", 180)]
    assert planner.parse_filter("age>1y") == [("age", ">", 365)]
    assert planner.parse_filter("author=sam") == [("text", "author", "sam", True)]
    assert planner.parse_filter("status!=done") == [("text", "status", "done", False)]
    assert planner.parse_filter("") == []
    # bare words search all text columns
    assert planner.parse_filter("brent") == [("text", "any", "brent", True)]
    assert planner.parse_filter("!wip") == [("text", "any", "wip", False)]


@pytest.mark.parametrize("bad", ["age>abc", "nope=x", "branch="])
def test_parse_filter_rejects_bad_terms(bad: str):
    with pytest.raises(ValueError):
        planner.parse_filter(bad)


def test_filter_branches():
    branches = make_branches()
    done = IssueInfo("ABC-2", "x", "Done", IssueState.DONE, "u")
    next(b for b in branches if b.name == "abc-2-open").issue = done

    def names(spec):
        return [b.name for b in planner.filter_branches(branches, planner.parse_filter(spec), ME)]

    assert names("!mine") == ["abc-3-theirs"]
    assert names("merged,mine") == ["abc-1-merged", "main"]
    assert names("age>90") == ["old-thing"]
    assert names("age<90,!merged,!local") == []
    assert names("branch=abc") == ["abc-1-merged", "abc-2-open", "abc-3-theirs"]
    assert names("status=done") == ["abc-2-open"]
    assert names("status!=done") == [b.name for b in branches if b.name != "abc-2-open"]
    assert names("!remote") == ["old-thing"]
    # bare word matches any text column (author email here)
    assert names("sarah") == ["abc-3-theirs"]
    assert names("old") == ["old-thing"]
    assert names("!brent") == ["abc-3-theirs"]


def test_extract_and_attach_issues():
    branches = make_branches()

    def extractor(name: str) -> str | None:
        return "ABC-1" if "abc-1" in name else None

    keys = planner.extract_keys(branches, extractor)
    assert keys == ["ABC-1"]
    issue = IssueInfo("ABC-1", "x", "Done", IssueState.DONE, "u")
    planner.attach_issues(branches, {"ABC-1": issue})
    merged_branch = next(b for b in branches if b.name == "abc-1-merged")
    assert merged_branch.issue is issue
