"""The log formatter must never be configurable into not redacting.

RedactingFormatter used to take a ``redact`` flag. Both call sites left it at
its default, but a formatter configured not to redact writes BOT_TOKEN, the
Mongo URI and session strings straight into Thunder/logs/bot.txt -- which /log
then serves. The switch is gone and redaction is unconditional.
"""

import logging

import pytest

from Thunder.utils.logger import REDACTED, RedactingFormatter, redact_secrets

# shapes taken from logger.py's own patterns -- a test that guesses the token
# format only proves the guess was wrong
SECRETS = {
    "bot token": "1234567890:" + "A" * 40,
    "mongo uri": "mongodb+srv://user:p4ssw0rd@cluster0.example.mongodb.net/thunder",
    "api hash": "api_hash=0123456789abcdef0123456789abcdef",
    "session string": "session_string=" + "B" * 45,
}


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("ThunderBot", logging.INFO, __file__, 1, message, None, None)


@pytest.mark.unit
def test_formatter_takes_no_opt_out():
    import inspect

    params = list(inspect.signature(RedactingFormatter.__init__).parameters)
    assert "redact" not in params, f"a redaction opt-out came back: {params}"


@pytest.mark.unit
@pytest.mark.parametrize("label", sorted(SECRETS))
def test_formatter_redacts_every_secret_family(label):
    secret = SECRETS[label]
    bare = secret.split("=", 1)[-1]
    fmt = RedactingFormatter("%(message)s")
    out = fmt.format(_record(f"connect {label}: {secret} done"))
    assert bare not in out, f"{label} survived redaction"
    assert REDACTED in out or "***" in out


@pytest.mark.unit
def test_formatter_redacts_a_traceback_too():
    fmt = RedactingFormatter("%(message)s")
    try:
        raise RuntimeError(f"auth failed for {SECRETS['mongo uri']}")
    except RuntimeError as e:
        record = logging.LogRecord(
            "ThunderBot", logging.ERROR, __file__, 1, "boom", None, (type(e), e, e.__traceback__)
        )
    out = fmt.format(record)
    assert "p4ssw0rd" not in out


@pytest.mark.unit
def test_redact_secrets_leaves_ordinary_text_alone():
    text = "Canonical record lookup failed (transport): server timeout"
    assert redact_secrets(text) == text
