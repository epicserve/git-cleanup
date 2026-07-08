import pytest

from git_cleanup.ui import format_age


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, "0d"),
        (12, "12d"),
        (30, "1m"),
        (45, "1m 15d"),
        (364, "12m 4d"),
        (365, "1y"),
        (400, "1y 1m 5d"),
        (730, "2y"),
        (919, "2y 6m 9d"),
    ],
)
def test_format_age(days: int, expected: str):
    assert format_age(days) == expected
