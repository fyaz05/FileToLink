# tests/test_unit/test_stream_routes.py
"""HTTP parsing primitives (H2 target) + L4 dual-hash + L6 disposition."""

import pytest
from aiohttp.web import HTTPBadRequest, HTTPRequestRangeNotSatisfiable

from Thunder.server.stream_routes import (
    build_content_disposition,
    parse_media_request,
    parse_range_header,
    validate_public_hash,
)


class TestParseMediaRequest:
    @pytest.mark.unit
    def test_hash_first(self):
        mid, h = parse_media_request("AbCdEf12345/video.mp4", {})
        assert mid == 12345
        assert h == "AbCdEf"

    @pytest.mark.unit
    def test_hash_first_with_trailing_slash_path(self):
        mid, h = parse_media_request("AbCdEf12345", {})
        assert mid == 12345

    @pytest.mark.unit
    def test_id_first_with_query_hash(self):
        mid, h = parse_media_request("12345/name.mp4", {"hash": "AbCdEf"})
        assert mid == 12345
        assert h == "AbCdEf"

    @pytest.mark.unit
    def test_invalid_hash_raises(self):
        from Thunder.server.exceptions import InvalidHash

        with pytest.raises(InvalidHash):
            parse_media_request("12345/name.mp4", {"hash": "short"})
        with pytest.raises(InvalidHash):
            parse_media_request("nonsense", {})

    @pytest.mark.unit
    def test_bad_message_id_raises(self):
        from Thunder.server.exceptions import InvalidHash

        with pytest.raises(InvalidHash):
            parse_media_request("AbCdEf_+*/x", {})


class TestValidatePublicHash:
    @pytest.mark.unit
    def test_accepts_legacy_20(self):
        assert validate_public_hash("a" * 20) == "a" * 20

    @pytest.mark.unit
    def test_accepts_new_32(self):
        assert validate_public_hash("b" * 32) == "b" * 32

    @pytest.mark.unit
    def test_rejects_other_lengths_and_normalizes_case(self):
        from Thunder.server.exceptions import InvalidHash

        with pytest.raises(InvalidHash):
            validate_public_hash("c" * 21)
        with pytest.raises(InvalidHash):
            validate_public_hash("g" * 32)  # not hex
        assert validate_public_hash("A" * 32) == "a" * 32  # lowercased


class TestParseRangeHeader:
    @pytest.mark.unit
    def test_full_file(self):
        assert parse_range_header("", 100) == (0, 99)

    @pytest.mark.unit
    def test_open_ended(self):
        assert parse_range_header("bytes=10-", 100) == (10, 99)

    @pytest.mark.unit
    def test_closed_range(self):
        assert parse_range_header("bytes=0-49", 100) == (0, 49)

    @pytest.mark.unit
    def test_suffix_range(self):
        assert parse_range_header("bytes=-10", 100) == (90, 99)

    @pytest.mark.unit
    def test_unsatisfiable_raises_416(self):
        with pytest.raises(HTTPRequestRangeNotSatisfiable) as exc:
            parse_range_header("bytes=99999999999-", 100)
        assert exc.value.headers["Content-Range"] == "bytes */100"

    @pytest.mark.unit
    def test_invalid_header_raises_400(self):
        with pytest.raises(HTTPBadRequest):
            parse_range_header("bytes=1-2-3", 100)


class TestContentDisposition:
    @pytest.mark.unit
    def test_ascii_fallback_plus_rfc5987(self):
        header = build_content_disposition("attachment", "video.mp4")
        assert 'filename="video.mp4"' in header
        assert "filename*=UTF-8''video.mp4" in header

    @pytest.mark.unit
    def test_non_latin_gets_ascii_fallback(self):
        header = build_content_disposition("attachment", "视频 file.mp4")
        assert header.startswith("attachment;")
        assert 'filename="' in header and "视频" not in header.split("filename*")[0]
        assert "%E8%A7%86%E9%A2%91" in header  # encoded filename*
