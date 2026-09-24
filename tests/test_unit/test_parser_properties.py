"""Property tests for security-adjacent parsers (stdlib random, fixed seed).

Deterministic (seeded) fuzzing over input spaces: range headers, canonical
hashes, media names. No network, no Mongo, no new dependencies.
"""

import random
import string

import pytest
from aiohttp import web

from Thunder.server.exceptions import InvalidHash
from Thunder.server.stream_routes import parse_range_header, validate_public_hash
from Thunder.utils.bot_utils import quote_media_name

pytestmark = pytest.mark.unit

_rng = random.Random(20260918)
_HEX = string.digits + "abcdef"
_SIZES = [1, 2, 99, 1023, 1024, 1025, 10 * 1024 * 1024, 2**31 - 1]


def _rand_size():
    return _rng.choice(_SIZES)


@pytest.mark.unit
def test_range_valid_spans_stay_in_bounds():
    """Any well-formed span resolves inside [0, size-1] with start <= end."""
    for _ in range(300):
        size = _rand_size()
        start = _rng.randint(0, size - 1)
        end = _rng.randint(start, size + 5000)  # oversized ends must clamp
        header = f"bytes={start}-{end}"
        got_start, got_end = parse_range_header(header, size)
        assert got_start == start
        assert got_end == min(end, size - 1)
        assert 0 <= got_start <= got_end < size
        assert got_end - got_start + 1 <= size


@pytest.mark.unit
def test_range_open_and_suffix_forms():
    for _ in range(200):
        size = _rand_size()
        start = _rng.randint(0, size - 1)
        assert parse_range_header(f"bytes={start}-", size) == (start, size - 1)
        n = _rng.randint(1, size + 100)
        got_start, got_end = parse_range_header(f"bytes=-{n}", size)
        assert (got_start, got_end) == (max(size - n, 0), size - 1)


@pytest.mark.unit
def test_range_garbage_is_400_and_oob_is_416():
    for bad in ("bytes=abc", "bytes=1-2,3-4", "bytes=", "items=0-1", "bytes=-", "bytes=5"):
        with pytest.raises(web.HTTPBadRequest):
            parse_range_header(bad, 1024)
    for _ in range(100):
        size = _rand_size()
        with pytest.raises(web.HTTPRequestRangeNotSatisfiable):
            parse_range_header(f"bytes={size}-{size + 10}", size)
        with pytest.raises(web.HTTPRequestRangeNotSatisfiable):
            parse_range_header("bytes=5-3", size)


@pytest.mark.unit
def test_416_carries_content_range():
    """Every 416 names the resource size so clients can retry correctly."""
    for _ in range(50):
        size = _rand_size()
        with pytest.raises(web.HTTPRequestRangeNotSatisfiable) as exc:
            parse_range_header(f"bytes={size}-", size)
        assert exc.value.headers["Content-Range"] == f"bytes */{size}"


@pytest.mark.unit
def test_canonical_hash_shapes():
    """20/32-hex (any case, padded) normalize to lowercase; else InvalidHash."""
    for _ in range(200):
        raw = "".join(_rng.choice(_HEX) for _ in range(_rng.choice((20, 32))))
        mixed = "".join(c.upper() if _rng.random() < 0.5 else c for c in raw)
        assert validate_public_hash(f"  {mixed}  ") == raw
    for bad in ("", "xyz", "a" * 19, "b" * 21, "c" * 31, "d" * 33, "e" * 32 + "!", "0x" + "1" * 30):
        with pytest.raises(InvalidHash):
            validate_public_hash(bad)


@pytest.mark.unit
def test_quoted_media_name_never_splits_path():
    """Quote_media_name output must never contain a path separator."""
    alphabet = string.ascii_letters + string.digits + " /._-()<>&\"'%\u00e9\u4e2d"
    for _ in range(300):
        name = "".join(_rng.choice(alphabet) for _ in range(_rng.randint(0, 120)))
        out = quote_media_name(name or "x")
        assert isinstance(out, str) and out
        assert "/" not in out
