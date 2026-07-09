from datetime import UTC, datetime, timedelta

import pytest

from git_cleanup.models import BranchInfo
from git_cleanup.ui import _sync_text, format_age


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
