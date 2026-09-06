import pytest

from Thunder.utils.time_format import get_readable_time


@pytest.mark.unit
@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0s"),
        (-10, "0s"),
        (59, "59s"),
        (60, "1m"),
        (61, "1m 1s"),
        (3600, "1h"),
        (3661, "1h 1m 1s"),
        (86400, "1d"),
        (90061, "1d 1h 1m 1s"),
    ],
)
def test_get_readable_time(seconds, expected):
    assert get_readable_time(seconds) == expected


@pytest.mark.unit
def test_non_int_input_is_handled():
    # float truncates; garbage returns "N/A" via the guard
    assert get_readable_time(90.9) == "1m 30s"
