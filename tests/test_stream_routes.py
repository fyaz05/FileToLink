import os

# Set required env vars before any Thunder imports
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "test_hash_value_1234567890abcdef")
os.environ.setdefault("BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
os.environ.setdefault("BIN_CHANNEL", "-1001234567890")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017/test")
os.environ.setdefault("OWNER_ID", "99999")

import pytest

from Thunder.server.exceptions import InvalidHash
from Thunder.server.stream_routes import (
    parse_media_request,
    parse_range_header,
    validate_public_hash,
)


class TestParseRangeHeader:
    def test_empty_range(self):
        start, end = parse_range_header("", 1000)
        assert start == 0
        assert end == 999

    def test_valid_range(self):
        start, end = parse_range_header("bytes=100-200", 1000)
        assert start == 100
        assert end == 200

    def test_range_to_end(self):
        start, end = parse_range_header("bytes=500-", 1000)
        assert start == 500
        assert end == 999

    def test_suffix_range(self):
        start, end = parse_range_header("bytes=-100", 1000)
        assert start == 900
        assert end == 999

    def test_invalid_range_raises(self):
        with pytest.raises(Exception):
            parse_range_header("invalid", 1000)


class TestParseMediaRequest:
    def test_hash_first_pattern(self):
        msg_id, hash_val = parse_media_request("abc12342/some_file.mp4", {})
        assert msg_id == 42
        assert hash_val == "abc123"

    def test_id_first_with_query(self):
        msg_id, hash_val = parse_media_request("42/some_file.mp4", {"hash": "abc123"})
        assert msg_id == 42
        assert hash_val == "abc123"

    def test_invalid_path_raises(self):
        with pytest.raises(InvalidHash):
            parse_media_request("invalid", {})


class TestValidatePublicKey:
    def test_valid_hash(self):
        h = "a" * 20
        assert validate_public_hash(h) == h

    def test_invalid_length_raises(self):
        with pytest.raises(InvalidHash):
            validate_public_hash("abc")

    def test_invalid_chars_raises(self):
        with pytest.raises(InvalidHash):
            validate_public_hash("g" * 20)
