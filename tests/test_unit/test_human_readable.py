import pytest

from Thunder.utils.human_readable import humanbytes


@pytest.mark.unit
@pytest.mark.parametrize(
    "size,expected",
    [
        (0, "0 B"),
        (-5, "-5 B"),  # characterization: truthy negatives pass through
        (1, "1 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024**2, "1.0 MB"),
        (1024**3, "1.0 GB"),
        (1024**5, "1.0 PB"),
        (1024**8, "1.0 YB"),
        (1024**9, "1024.0 YB"),  # clamped at last unit
    ],
)
def test_humanbytes(size, expected):
    assert humanbytes(size) == expected


@pytest.mark.unit
def test_humanbytes_decimal_places():
    assert humanbytes(1536, decimal_places=0) == "2.0 KB"
    assert humanbytes(1536, decimal_places=3) == "1.5 KB"


@pytest.mark.unit
def test_humanbytes_huge_does_not_raise():
    assert humanbytes(10**30).endswith("YB")
