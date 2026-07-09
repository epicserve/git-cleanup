from datetime import UTC, datetime, timedelta

import pytest

from git_cleanup.models import BranchInfo, IssueInfo, IssueState
from git_cleanup.ui import _choice_rows, _sync_text, format_age


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


def test_choice_rows_aligned():
    done = IssueInfo("ABC-123", "x", "Done", IssueState.DONE, "u")
    branches = [
        make_branch(name="abc-123-fix-login", merged=True, issue_key="ABC-123", issue=done),
        make_branch(name="short", ahead=2),
        make_branch(
            name="a-very-long-branch-name-that-exceeds-the-forty-char-cap",
            committed_at=datetime.now(UTC) - timedelta(days=919),
        ),
    ]
    header, rows = _choice_rows(branches)

    assert len(rows) == len(branches)
    # every column label starts at the same offset in header and all rows
    for label in ("BRANCH", "SYNC", "AUTHOR", "AGE", "MRG", "ISSUE", "STATUS"):
        offset = header.index(label)
        assert offset >= 0
    for row in rows:
        assert row.startswith(("abc-123", "short", "a-very-long"))

    sync_col = header.index("SYNC")
    assert rows[0][sync_col:].startswith("✓")
    assert rows[1][sync_col:].startswith("↑2")

    merged_col = header.index("MRG")
    assert rows[0][merged_col] == "✓"
    assert rows[1][merged_col] == " "

    issue_col = header.index("ISSUE")
    assert rows[0][issue_col:].startswith("ABC-123")
    assert rows[1][issue_col:].startswith("—")
    assert rows[0][header.index("STATUS"):].startswith("Done")

    # long name truncated with ellipsis at the 40-char cap
    assert "…" in rows[2]
    assert rows[2].index("…") <= 40
    # age in y/m/d form
    assert "2y" in rows[2]
