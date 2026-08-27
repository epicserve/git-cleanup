from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from git_cleanup import planner
from git_cleanup.gitops import RawRef
from git_cleanup.models import Action, BranchInfo, IssueInfo, IssueState, WorktreeAction
from tests.conftest import raw_stash, raw_worktree

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
    defaults = {"current": "main", "default": "main", "protected": frozenset({"main"})}
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


def test_recommend_actions_mine():
    recs = planner.recommend_actions(make_branches(), for_email=ME, archive_age_days=90)
    assert recs == {
        "abc-1-merged": Action.DELETE,
        "old-thing": Action.ARCHIVE,
    }


def test_recommend_actions_include_all():
    recs = planner.recommend_actions(
        make_branches(), for_email=ME, include_all=True, archive_age_days=90
    )
    assert recs["abc-3-theirs"] is Action.DELETE  # sarah's merged branch


def test_recommend_actions_team_report():
    # for_email=None recommends across all authors — the CI report case
    recs = planner.recommend_actions(make_branches(), archive_age_days=90)
    assert recs["abc-1-merged"] is Action.DELETE
    assert recs["abc-3-theirs"] is Action.DELETE
    assert recs["old-thing"] is Action.ARCHIVE


def test_recommend_actions_issue_done_makes_deletable():
    branches = make_branches()
    open_branch = next(b for b in branches if b.name == "abc-2-open")
    open_branch.issue_key = "ABC-2"
    open_branch.issue = IssueInfo("ABC-2", "x", "Done", IssueState.DONE, "u")
    recs = planner.recommend_actions(branches, for_email=ME, archive_age_days=90)
    assert recs["abc-2-open"] is Action.DELETE


def test_recommend_actions_never_protected():
    recs = planner.recommend_actions(make_branches(), archive_age_days=0)
    assert "main" not in recs


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


def test_format_sort_roundtrip():
    assert planner.format_sort(planner.parse_sort("-age,author")) == "-age,author"
    assert planner.format_sort(planner.parse_sort(planner.DEFAULT_SORT)) == "branch"


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
    assert planner.parse_filter("mine,!merged") == [
        ("bool", "mine", True),
        ("bool", "merged", False),
    ]
    assert planner.parse_filter("merged=true") == [("bool", "merged", True)]
    assert planner.parse_filter("local=false") == [("bool", "local", False)]
    assert planner.parse_filter("worktree!=true") == [("bool", "worktree", False)]
    assert planner.parse_filter("gone=TRUE") == [("bool", "gone", True)]
    assert planner.parse_filter("age>90") == [("age", ">", 90)]
    assert planner.parse_filter("age<=6m") == [("age", "<=", 180)]
    assert planner.parse_filter("age>1y") == [("age", ">", 365)]
    assert planner.parse_filter("author=sam") == [("text", "author", "sam", True)]
    assert planner.parse_filter("author=sam|chris") == [("text", "author", "sam|chris", True)]
    assert planner.parse_filter("status!=done") == [("text", "status", "done", False)]
    assert planner.parse_filter("") == []
    # an empty value is a real term: "is the column set?"
    assert planner.parse_filter("status=") == [("text", "status", "", True)]
    assert planner.parse_filter("status!=") == [("text", "status", "", False)]
    assert planner.parse_filter("issue= ") == [("text", "issue", "", True)]
    # bare words search all text columns
    assert planner.parse_filter("brent") == [("text", "any", "brent", True)]
    assert planner.parse_filter("brent|sarah") == [("text", "any", "brent|sarah", True)]
    assert planner.parse_filter("!wip") == [("text", "any", "wip", False)]


@pytest.mark.parametrize("bad", ["age>abc", "nope=x", "nope=", "!"])
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
    # abc-2-open is the only branch with an issue attached, so the only one with a status
    assert names("status!=") == ["abc-2-open"]
    assert names("status=") == [b.name for b in branches if b.name != "abc-2-open"]
    assert names("!remote") == ["old-thing"]
    assert names("merged=true") == names("merged")
    assert names("local=false") == names("!local")
    assert names("remote!=true") == names("!remote")
    # bare word matches any text column (author email here)
    assert names("sarah") == ["abc-3-theirs"]
    assert names("old") == ["old-thing"]
    assert names("!brent") == ["abc-3-theirs"]
    # '|' is OR inside one text term; comma is still AND
    assert names("author=brent|sarah") == [b.name for b in branches]
    assert names("author=sarah|nobody") == ["abc-3-theirs"]
    assert names("author!=brent|sarah") == []
    assert names("abc-1|old") == ["abc-1-merged", "old-thing"]
    assert names("author=brent|sarah,!merged") == ["abc-2-open", "old-thing"]
    # empty pieces around '|' do not change the term; surrounding spaces are ignored
    assert names("author=sarah|") == ["abc-3-theirs"]
    assert names("author=sarah | nobody") == ["abc-3-theirs"]
    assert names("author=|") == names("author=")


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


# ---------- worktrees ----------


def branch_for(name: str, **overrides) -> BranchInfo:
    defaults = {
        "name": name,
        "has_local": True,
        "has_remote": True,
        "sha": f"sha-{name}",
        "author_name": "X",
        "author_email": ME,
        "committed_at": NOW - timedelta(days=5),
        "merged": False,
    }
    defaults.update(overrides)
    return BranchInfo(**defaults)


def test_build_worktrees_joins_and_back_fills():
    branches = [branch_for("feat"), branch_for("other")]
    worktrees = planner.build_worktrees(
        [raw_worktree("/repo", "main"), raw_worktree("/wt/feat", "feat")],
        branches,
    )
    assert worktrees[0].is_main and not worktrees[1].is_main  # index 0 is main
    assert worktrees[1].branch_info is branches[0]
    # back-filled so the Branches tab can show a WT indicator
    assert branches[0].worktree_path == Path("/wt/feat")
    assert branches[0].has_worktree
    assert not branches[1].has_worktree


def test_build_worktrees_is_current_normalizes_path():
    """Lexical normpath only — the planner does no filesystem I/O."""
    worktrees = planner.build_worktrees(
        [raw_worktree("/repo", "main"), raw_worktree("/a/b/wt", "feat")],
        [],
        current_path=Path("/a/b/../b/wt"),
    )
    assert not worktrees[0].is_current
    assert worktrees[1].is_current
    assert not worktrees[1].removable  # you cannot remove the one you are in


def test_build_worktrees_detached_and_bare_have_no_branch():
    detached, bare = planner.build_worktrees(
        [raw_worktree("/wt/det", None, detached=True), raw_worktree("/bare", None, bare=True)],
        [branch_for("feat")],
    )
    assert detached.branch_info is None and detached.age_days is None
    assert not detached.merged and not detached.issue_done
    assert not detached.is_mine(ME)
    assert bare.branch_info is None


def test_build_worktrees_dirty_counts_mapping():
    worktrees = planner.build_worktrees(
        [raw_worktree("/repo", "main"), raw_worktree("/wt/a", "feat")],
        [],
        dirty_counts={Path("/wt/a"): 3},
    )
    assert worktrees[1].dirty_count == 3 and worktrees[1].is_dirty
    # a path absent from the mapping (e.g. a bare worktree) reads as unknown
    assert worktrees[0].dirty_count is None and not worktrees[0].is_dirty


def test_build_worktrees_duplicate_branch_first_wins():
    """A broken entry can still name a branch a live worktree also holds."""
    branch = branch_for("feat")
    planner.build_worktrees(
        [
            raw_worktree("/repo", "main"),
            raw_worktree("/wt/live", "feat"),
            raw_worktree("/wt/broken", "feat", prunable=True),
        ],
        [branch],
    )
    assert branch.worktree_path == Path("/wt/live")


def recommend(worktrees, **kwargs):
    return planner.recommend_worktree_actions(worktrees, **kwargs)


def wt_infos(raws, branches, **kwargs):
    return planner.build_worktrees(raws, branches, **kwargs)


def test_recommend_worktree_merged_clean_is_marked():
    branch = branch_for("feat", merged=True)
    worktrees = wt_infos([raw_worktree("/repo", "main"), raw_worktree("/wt/a", "feat")], [branch])
    assert recommend(worktrees, for_email=ME) == {"/wt/a": WorktreeAction.REMOVE}


def test_recommend_worktree_never_premarks_dirty():
    """Pre-marking must not set up a --force that discards uncommitted work."""
    branch = branch_for("feat", merged=True)
    worktrees = wt_infos(
        [raw_worktree("/repo", "main"), raw_worktree("/wt/a", "feat")],
        [branch],
        dirty_counts={Path("/wt/a"): 1},
    )
    assert recommend(worktrees, for_email=ME) == {}


def test_recommend_worktree_issue_done_is_marked():
    done = IssueInfo("ABC-1", "x", "Done", IssueState.DONE, "u")
    branch = branch_for("feat", issue=done)
    worktrees = wt_infos([raw_worktree("/repo", "main"), raw_worktree("/wt/a", "feat")], [branch])
    assert recommend(worktrees, for_email=ME) == {"/wt/a": WorktreeAction.REMOVE}


def test_recommend_worktree_prunable_ignores_authorship():
    """The directory is already gone: nothing is at risk and the authorship of a
    vanished checkout is meaningless."""
    branch = branch_for("theirs", author_email=OTHER)
    worktrees = wt_infos(
        [raw_worktree("/repo", "main"), raw_worktree("/wt/gone", "theirs", prunable=True)],
        [branch],
    )
    assert recommend(worktrees, for_email=ME, include_all=False) == {
        "/wt/gone": WorktreeAction.REMOVE
    }


def test_recommend_worktree_authorship_gate():
    branch = branch_for("theirs", merged=True, author_email=OTHER)
    worktrees = wt_infos([raw_worktree("/repo", "main"), raw_worktree("/wt/a", "theirs")], [branch])
    assert recommend(worktrees, for_email=ME) == {}
    assert recommend(worktrees, for_email=ME, include_all=True) == {"/wt/a": WorktreeAction.REMOVE}


def test_recommend_worktree_skips_unremovable():
    branch = branch_for("feat", merged=True)
    main_wt, locked, current = wt_infos(
        [
            raw_worktree("/repo", "main"),
            raw_worktree("/wt/locked", "feat", locked=True),
            raw_worktree("/wt/here", "feat"),
        ],
        [branch, branch_for("main", merged=True)],
        current_path=Path("/wt/here"),
    )
    assert recommend([main_wt, locked, current], for_email=ME) == {}


def test_recommend_worktree_skips_protected_default_and_current_branches():
    protected = branch_for("develop", merged=True, is_protected=True)
    default = branch_for("main", merged=True, is_default=True)
    checked_out = branch_for("wip", merged=True, is_current=True)
    worktrees = wt_infos(
        [
            raw_worktree("/repo", "trunk"),
            raw_worktree("/wt/develop", "develop"),
            raw_worktree("/wt/main", "main"),
            raw_worktree("/wt/wip", "wip"),
        ],
        [protected, default, checked_out],
    )
    assert recommend(worktrees, for_email=ME) == {}


def test_recommend_worktree_prunable_wins_over_protected_branch():
    """A vanished checkout of a protected branch is still just bookkeeping."""
    protected = branch_for("develop", is_protected=True)
    worktrees = wt_infos(
        [raw_worktree("/repo", "main"), raw_worktree("/wt/gone", "develop", prunable=True)],
        [protected],
    )
    assert recommend(worktrees, for_email=ME) == {"/wt/gone": WorktreeAction.REMOVE}


def test_worktree_filter_and_sort_terms():
    assert planner.parse_filter("worktree") == [("bool", "worktree", True)]
    assert planner.parse_filter("!worktree") == [("bool", "worktree", False)]
    assert planner.parse_filter("worktree=true") == [("bool", "worktree", True)]
    assert planner.parse_filter("worktree=false") == [("bool", "worktree", False)]
    with pytest.raises(ValueError, match="true or false"):
        planner.parse_filter("worktree=x")
    assert planner.parse_sort("worktree") == [("worktree", False)]
    assert "worktree" in planner.SORT_COLUMNS


def test_worktree_filter_selects_branches():
    with_wt = branch_for("has-wt", worktree_path=Path("/wt/a"))
    without = branch_for("no-wt")
    branches = [with_wt, without]

    def names(spec):
        return [b.name for b in planner.filter_branches(branches, planner.parse_filter(spec), ME)]

    assert names("worktree") == ["has-wt"]
    assert names("!worktree") == ["no-wt"]
    # escape hatch for the old bare-word substring behavior
    assert names("branch=worktree") == []


def test_worktree_sort_orders_by_presence():
    branches = [branch_for("a"), branch_for("b", worktree_path=Path("/wt/b"))]
    result = planner.sort_branches(branches, planner.parse_sort("-worktree"))
    assert [b.name for b in result] == ["b", "a"]


# ---------- stashes ----------


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("On main: hello", ("main", "hello", False)),
        # branch names cannot contain ':', so the message keeps its later ': '
        ("On feature/x-1: fix: the thing", ("feature/x-1", "fix: the thing", False)),
        ("WIP on main: 1a2b3c4 add login", ("main", "1a2b3c4 add login", True)),
        # "(no branch)" is a detached-HEAD sentinel, not a branch
        ("On (no branch): detached", (None, "detached", False)),
        ("WIP on (no branch): 1a2b3c4 subj", (None, "1a2b3c4 subj", True)),
        # `git stash store -m X` writes X verbatim: shown whole, not mangled
        ("stored by hand", (None, "stored by hand", False)),
        ("Onmain: x", (None, "Onmain: x", False)),
        ("on main: x", (None, "on main: x", False)),  # case is exact
    ],
)
def test_parse_stash_subject(subject: str, expected: tuple):
    assert planner.parse_stash_subject(subject) == expected


def test_build_stashes_maps_fields_and_file_counts():
    stashes = planner.build_stashes(
        [raw_stash(0, "On main: named"), raw_stash(1, "On main: other")],
        file_counts={"stash@{0}": 3},
    )
    assert (stashes[0].branch, stashes[0].message) == ("main", "named")
    assert stashes[0].file_count == 3
    assert stashes[1].file_count is None  # absent from the mapping


def test_build_stashes_marks_untracked_from_parent_count():
    plain, with_untracked = planner.build_stashes(
        [
            raw_stash(0, "On main: plain"),
            raw_stash(1, "On main: unt", parents=("p1", "p2", "p3")),
        ]
    )
    assert not plain.has_untracked
    assert with_untracked.has_untracked


def test_build_stashes_never_reorders_by_date():
    """Reflog order is not date order — stash@{0} can be older than stash@{1} —
    so reordering would desync the numbers the user reads from the selectors the
    executor acts on."""
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 6, 1, tzinfo=UTC)
    stashes = planner.build_stashes(
        [
            raw_stash(0, "On main: older but first", created_at=older),
            raw_stash(1, "On main: newer but second", created_at=newer),
        ]
    )
    assert [s.index for s in stashes] == [0, 1]
    assert stashes[0].created_at == older
