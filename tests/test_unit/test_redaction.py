"""H10: shared redaction regexes."""

import pytest

from Thunder.utils.logger import hash_path_token, redact_secrets


@pytest.mark.unit
def test_bot_token_redacted():
    text = "starting bot with token 1234567890:ABCdef-_GHIjklMNOpqrsTUVwxyz1234567"
    out = redact_secrets(text)
    assert "1234567890:ABCdef" not in out
    assert "***REDACTED***" in out


@pytest.mark.unit
def test_mongo_uri_redacted():
    text = "connecting to mongodb+srv://user:supersecret@cluster.example.net/db"
    out = redact_secrets(text)
    assert "supersecret" not in out


@pytest.mark.unit
def test_clean_text_untouched():
    text = "no secrets here, just 42 and a link https://example.com/f/abc/file"
    assert redact_secrets(text) == text


@pytest.mark.unit
def test_hash_path_token_is_stable_and_short():
    a = hash_path_token("1234567890:ABCdef-_GHIjklMNOpqrsTUVwxyz1234567")
    b = hash_path_token("1234567890:ABCdef-_GHIjklMNOpqrsTUVwxyz1234567")
    c = hash_path_token("different")
    assert a == b and a != c and len(a) == 8
