"""HTTP parsing primitives (H2 target) + L4 dual-hash + L6 disposition."""

from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.web import HTTPBadRequest, HTTPRequestRangeNotSatisfiable

from Thunder.server.stream_routes import (
    _is_activation_token,
    _telegram_activate_url,
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
    def test_end_beyond_eof_clamps_instead_of_416(self):
        # RFC 7233: last-byte-pos >= length means "rest of the file"; download
        # managers send such fixed-chunk ends, and a hard 416 broke their resume/seeking.
        assert parse_range_header("bytes=0-99999999", 100) == (0, 99)
        assert parse_range_header("bytes=50-1000", 100) == (50, 99)

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


class TestActivationTokenShape:
    @pytest.mark.unit
    def test_accepts_real_token_urlsafe_shape(self):
        import secrets

        assert _is_activation_token(secrets.token_urlsafe(32))

    @pytest.mark.unit
    def test_rejects_wrong_length_and_chars(self):
        assert not _is_activation_token("short")
        assert not _is_activation_token("a" * 42)
        assert not _is_activation_token("a" * 44)
        assert not _is_activation_token("a" * 42 + "$$")
        # redirect / header injection payloads must fail the shape check
        assert not _is_activation_token("../../evil.com?")
        assert not _is_activation_token("x\r\nLocation: https://evil.com")
        assert not _is_activation_token("a" * 20 + "/" + "b" * 22)


class TestTelegramActivateUrl:
    @pytest.mark.unit
    def test_valid_token_produces_expected_deep_link(self):
        token = "a" * 43
        assert _telegram_activate_url("MyBot", token) == f"https://t.me/MyBot?start={token}"

    @pytest.mark.unit
    def test_hostile_token_cannot_reshape_url(self):
        url = _telegram_activate_url("MyBot", "x&start=evil#frag")
        assert url.startswith("https://t.me/MyBot?start=")
        assert "&" not in url[24:] and "#" not in url[24:]


class TestCanonicalDeliveryErrorLadder:
    """Review fix regression: transport errors must 503 WITHOUT deleting the
    record; only true Telegram-side absence may self-heal (delete)."""

    @staticmethod
    def _request():
        return SimpleNamespace(match_info={"secure_hash": "a" * 32})

    @pytest.mark.unit
    async def test_transport_error_maps_to_503_and_never_deletes(self, monkeypatch):
        import Thunder.server.stream_routes as stream_routes
        from Thunder.server.exceptions import TelegramUnavailable

        deleted: list[dict] = []

        async def get_message(_ref):
            raise TelegramUnavailable("FloodWait exhausted")

        async def get_file_by_hash(_h, raise_on_error=False):
            return {"file_unique_id": "u1", "canonical_message_id": 123, "file_size": 10}

        async def forget_stale_record(record):
            deleted.append(record)

        monkeypatch.setattr(stream_routes, "get_file_by_hash", get_file_by_hash)
        monkeypatch.setattr(
            stream_routes,
            "select_optimal_client",
            lambda: (0, SimpleNamespace(get_message=get_message)),
        )
        monkeypatch.setattr(stream_routes, "forget_stale_record", forget_stale_record)
        monkeypatch.setattr(stream_routes, "work_loads", {0: 0})

        with pytest.raises(web.HTTPServiceUnavailable) as exc:
            await stream_routes.canonical_media_delivery(self._request())
        assert exc.value.headers.get("Retry-After") == "5"
        assert deleted == []  # transient error must NOT destroy the record
        assert stream_routes.work_loads == {0: 0}  # admission slot released

    @pytest.mark.unit
    async def test_true_absence_self_heals_and_maps_to_404(self, monkeypatch):
        import Thunder.server.stream_routes as stream_routes
        from Thunder.server.exceptions import FileNotFound

        deleted: list[dict] = []

        async def get_message(_ref):
            raise FileNotFound("no such message")

        async def get_file_by_hash(_h, raise_on_error=False):
            return {"file_unique_id": "u1", "canonical_message_id": 123, "file_size": 10}

        async def forget_stale_record(record):
            deleted.append(record)

        monkeypatch.setattr(stream_routes, "get_file_by_hash", get_file_by_hash)
        monkeypatch.setattr(
            stream_routes,
            "select_optimal_client",
            lambda: (0, SimpleNamespace(get_message=get_message)),
        )
        monkeypatch.setattr(stream_routes, "forget_stale_record", forget_stale_record)
        monkeypatch.setattr(stream_routes, "work_loads", {0: 0})

        with pytest.raises(web.HTTPNotFound):
            await stream_routes.canonical_media_delivery(self._request())
        assert len(deleted) == 1  # genuine absence is the one self-heal case
        assert stream_routes.work_loads == {0: 0}

    @pytest.mark.unit
    async def test_medialess_vault_message_self_heals(self, monkeypatch):
        import Thunder.server.stream_routes as stream_routes

        deleted: list[dict] = []

        async def get_message(_ref):
            return SimpleNamespace()  # message exists but carries no media

        async def get_file_by_hash(_h, raise_on_error=False):
            return {"file_unique_id": "u1", "canonical_message_id": 123, "file_size": 10}

        async def forget_stale_record(record):
            deleted.append(record)

        monkeypatch.setattr(stream_routes, "get_file_by_hash", get_file_by_hash)
        monkeypatch.setattr(
            stream_routes,
            "select_optimal_client",
            lambda: (0, SimpleNamespace(get_message=get_message)),
        )
        monkeypatch.setattr(stream_routes, "forget_stale_record", forget_stale_record)
        monkeypatch.setattr(stream_routes, "get_media", lambda m: None)
        monkeypatch.setattr(stream_routes, "work_loads", {0: 0})

        with pytest.raises(web.HTTPNotFound):
            await stream_routes.canonical_media_delivery(self._request())
        assert len(deleted) == 1
