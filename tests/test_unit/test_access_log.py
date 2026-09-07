"""H10 access-log middleware: path pseudonymization + log-forging escape."""

import pytest

import Thunder.server as server_mod
from Thunder.server import _escape_control_chars, _redact_path

# 6 alnum + 2 trailing digits: the old id-first rule used to re-match this
# shape and hash it a second time.
_FAKE_PSEUDONYM = "ab12cd99"


@pytest.fixture(name="fake_hash")
def _fake_hash(monkeypatch):
    """Deterministic hash_path_token with a digit-tailed output."""
    calls: list[str] = []

    def _fake(token: str) -> str:
        calls.append(token)
        return _FAKE_PSEUDONYM

    monkeypatch.setattr(server_mod, "hash_path_token", _fake)
    return calls


@pytest.mark.unit
def test_canonical_pseudonym_hashed_exactly_once(fake_hash):
    path = "/f/" + "a" * 32 + "/video.mp4"
    out = _redact_path(path)
    assert out == f"/f/{_FAKE_PSEUDONYM}/video.mp4"
    # one input hashed, and the output never re-hashed
    assert fake_hash == ["a" * 32]
    assert "…" not in out


@pytest.mark.unit
def test_legacy_watch_segment_suffixed(fake_hash):
    out = _redact_path("/watch/AbCdEf12345/name.mp4")
    assert out == f"/watch/{_FAKE_PSEUDONYM}…/name.mp4"
    assert fake_hash == ["AbCdEf12345"]


@pytest.mark.unit
def test_id_first_segment_suffixed(fake_hash):
    out = _redact_path("/AbCdEf12345/name.mp4")
    assert out == f"/{_FAKE_PSEUDONYM}…/name.mp4"
    assert fake_hash == ["AbCdEf12345"]


@pytest.mark.unit
def test_bare_legacy_segment_suffixed(fake_hash):
    # review fix regression: the no-filename legacy URL is served by the
    # catch-all route; its bare hash+id is the whole link credential
    out = _redact_path("/AbCdEf12345")
    assert out == f"/{_FAKE_PSEUDONYM}…"
    assert fake_hash == ["AbCdEf12345"]


@pytest.mark.unit
def test_bare_id_only_segment_suffixed(fake_hash):
    # id-first family without filename (hash rides in the query string)
    out = _redact_path("/12345678")
    assert out == f"/{_FAKE_PSEUDONYM}…"
    assert fake_hash == ["12345678"]


@pytest.mark.unit
def test_legacy_20_char_canonical_hash(fake_hash):
    out = _redact_path("/f/" + "b" * 20 + "/v")
    assert out == f"/f/{_FAKE_PSEUDONYM}/v"
    assert fake_hash == ["b" * 20]


@pytest.mark.unit
def test_activation_token_redacted(fake_hash):
    out = _redact_path("/activate/" + "T" * 43)
    assert out == f"/activate/{_FAKE_PSEUDONYM}"
    assert fake_hash == ["T" * 43]


@pytest.mark.unit
def test_plain_paths_untouched(fake_hash):
    assert fake_hash == []
    for path in ("/health", "/status", "/watch/", "/"):
        assert _redact_path(path) == path
    assert fake_hash == []


@pytest.mark.unit
def test_short_id_segment_left_alone(fake_hash):
    # fewer than 6 hash chars: not the legacy capability shape
    assert _redact_path("/123/v.mp4") == "/123/v.mp4"
    assert fake_hash == []


@pytest.mark.unit
def test_real_pseudonym_stable_and_bare():
    # with the real hasher: deterministic, and canonical output never
    # carries the legacy truncation marker
    out1 = _redact_path("/f/" + "c" * 32 + "/v")
    out2 = _redact_path("/f/" + "c" * 32 + "/v")
    assert out1 == out2
    assert "…" not in out1
    pseudonym = out1.split("/f/")[1].split("/")[0]
    assert len(pseudonym) == 8
    int(pseudonym, 16)  # 8-hex


@pytest.mark.unit
def test_control_chars_escaped():
    forged = "/f/x\n[INFO] fake line\r\t"
    out = _escape_control_chars(forged)
    assert "\n" not in out and "\r" not in out and "\t" not in out
    assert "%0A" in out and "[INFO] fake line" in out


@pytest.mark.unit
def test_printable_path_untouched():
    path = "/f/abc/file.mp4?q=1"
    assert _escape_control_chars(path) == path
