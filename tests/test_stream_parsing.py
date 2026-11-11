"""Request parsing, hash validation and range arithmetic."""
import pytest

from Thunder.server.exceptions import InvalidHash
from Thunder.server.stream_routes import (
    MAX_CONCURRENT_PER_CLIENT,
    get_content_disposition,
    parse_media_request,
    parse_range_header,
    select_optimal_client,
    validate_public_hash,
)
from Thunder.bot import work_loads


class FakeRequest:
    def __init__(self, query=None):
        self.query = query or {}


class TestParseMediaRequest:
    def test_hash_first(self):
        message_id, secure_hash = parse_media_request("AbCdEf12345", {})
        assert message_id == 12345
        assert secure_hash == "AbCdEf"

    def test_hash_first_with_filename(self):
        message_id, secure_hash = parse_media_request("AbCdEf12345/video.mp4", {})
        assert message_id == 12345
        assert secure_hash == "AbCdEf"

    def test_id_first_with_query_hash(self):
        message_id, secure_hash = parse_media_request("12345", {"hash": "AbCdEf"})
        assert message_id == 12345
        assert secure_hash == "AbCdEf"

    def test_rejects_missing_hash(self):
        with pytest.raises(InvalidHash):
            parse_media_request("12345", {})

    def test_rejects_short_hash(self):
        with pytest.raises(InvalidHash):
            parse_media_request("12345", {"hash": "abc"})

    def test_rejects_illegal_characters(self):
        with pytest.raises(InvalidHash):
            parse_media_request("12345", {"hash": "ab/def"})

    def test_unquotes_path(self):
        message_id, secure_hash = parse_media_request("AbCdEf12345/my%20file.mp4", {})
        assert message_id == 12345
        assert secure_hash == "AbCdEf"

    def test_requires_slash_before_filename(self):
        # Without the separator the id and filename are indistinguishable.
        with pytest.raises(InvalidHash):
            parse_media_request("AbCdEf12345name.mp4", {})


class TestValidatePublicHash:
    def test_accepts_20_hex(self):
        value = "a" * 20
        assert validate_public_hash(value) == value

    def test_rejects_wrong_length(self):
        with pytest.raises(InvalidHash):
            validate_public_hash("a" * 19)

    def test_rejects_non_hex(self):
        with pytest.raises(InvalidHash):
            validate_public_hash("z" * 20)


class TestParseRangeHeader:
    def test_no_range_returns_whole_file(self):
        assert parse_range_header("", 1000) == (0, 999)

    def test_explicit_range(self):
        assert parse_range_header("bytes=100-199", 1000) == (100, 199)

    def test_open_ended_range(self):
        assert parse_range_header("bytes=500-", 1000) == (500, 999)

    def test_suffix_range(self):
        assert parse_range_header("bytes=-100", 1000) == (900, 999)

    def test_rejects_non_bytes_unit(self):
        with pytest.raises(Exception):
            parse_range_header("items=0-10", 1000)

    def test_rejects_start_past_end_of_file(self):
        with pytest.raises(Exception):
            parse_range_header("bytes=2000-3000", 1000)


class TestContentDisposition:
    def test_defaults_to_attachment(self):
        assert get_content_disposition(FakeRequest()) == "attachment"

    def test_accepts_inline(self):
        assert get_content_disposition(FakeRequest({"disposition": "inline"})) == "inline"

    def test_rejects_unknown_value(self):
        assert get_content_disposition(FakeRequest({"disposition": "evil"})) == "attachment"


class TestSelectOptimalClient:
    def test_picks_least_loaded(self, monkeypatch):
        work_loads.clear()
        work_loads[0] = 5
        work_loads[1] = 1
        monkeypatch.setattr(
            "Thunder.server.stream_routes.get_streamer", lambda cid: object())
        client_id, _ = select_optimal_client()
        assert client_id == 1

    def test_raises_when_all_clients_saturated(self):
        work_loads.clear()
        work_loads[0] = MAX_CONCURRENT_PER_CLIENT
        with pytest.raises(Exception) as exc:
            select_optimal_client()
        assert exc.value.status == 503
        assert work_loads[0] == MAX_CONCURRENT_PER_CLIENT

    def test_raises_when_no_clients(self):
        work_loads.clear()
        with pytest.raises(Exception):
            select_optimal_client()
