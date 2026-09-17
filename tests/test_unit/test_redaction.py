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


@pytest.mark.unit
@pytest.mark.parametrize(
    "text,leaked",
    [
        # API_HASH_PATTERN: api_hash[:=] + 32 hex (contextual so file hashes still log)
        ("config api_hash = '0123456789abcdef0123456789abcdef' done", "0123456789abcdef"),
        # SESSION_STRING_PATTERN: session_string[:=] + 40+ base64url chars
        ("login session_string='AbC123_-xYzAbC123_-xYzAbC123_-xYzAbC123_-xYz' ok", "AbC123_-xYz"),
        # ACTIVATION_TOKEN_PATTERN: ?start= + 43 urlsafe chars
        ("open https://t.me/MyBot?start=" + "a" * 43 + " now", "a" * 43),
    ],
)
def test_secret_patterns_redacted(text, leaked):
    out = redact_secrets(text)
    assert leaked not in out
    assert "***REDACTED***" in out
