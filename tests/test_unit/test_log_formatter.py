"""No formatter may be configurable into not redacting.

RedactingFormatter used to take a ``redact`` flag. Both call sites left it at
its default, but a formatter configured not to redact writes BOT_TOKEN, the
Mongo URI and session strings straight into Thunder/logs/bot.txt -- which /log
then serves. The switch is gone and redaction is unconditional.

These tests are parametrised over BOTH formatters the module can install.
They were not: the plain one had five tests and JsonFormatter had none, so
deleting redact_secrets() from JsonFormatter.format left the whole suite green
while LOG_FORMAT=json sent the Mongo URI straight into the file /log uploads.
"""

import json
import logging

import pytest

from Thunder.utils.logger import (
    REDACTED,
    JsonFormatter,
    RedactingFormatter,
    redact_secrets,
)

# shapes taken from logger.py's own patterns -- a test that guesses the token
# format only proves the guess was wrong
SECRETS = {
    "bot token": "1234567890:" + "A" * 40,
    "mongo uri": "mongodb+srv://user:p4ssw0rd@cluster0.example.mongodb.net/thunder",
    "api hash": "api_hash=0123456789abcdef0123456789abcdef",
    "session string": "session_string=" + "B" * 45,
}

# every formatter logger.py can install on the file handler, which is the one
# that writes bot.txt
FORMATTERS = {
    "plain": lambda: RedactingFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"),
    "json": JsonFormatter,
}


def _record(message, args=None) -> logging.LogRecord:
    return logging.LogRecord("ThunderBot", logging.INFO, __file__, 1, message, args, None)


def _raising_record(message="boom") -> logging.LogRecord:
    try:
        raise RuntimeError(f"auth failed for {SECRETS['mongo uri']}")
    except RuntimeError as e:
        return logging.LogRecord(
            "ThunderBot", logging.ERROR, __file__, 1, message, None, (type(e), e, e.__traceback__)
        )


@pytest.mark.unit
def test_formatter_takes_no_opt_out():
    import inspect

    params = list(inspect.signature(RedactingFormatter.__init__).parameters)
    assert "redact" not in params, f"a redaction opt-out came back: {params}"


@pytest.mark.unit
@pytest.mark.parametrize("fmt_name", sorted(FORMATTERS))
@pytest.mark.parametrize("label", sorted(SECRETS))
def test_formatter_redacts_every_secret_family(fmt_name, label):
    """The mutation this file exists for: drop redact_secrets() from either
    formatter and the corresponding case must fail."""
    secret = SECRETS[label]
    bare = secret.split("=", 1)[-1]
    out = FORMATTERS[fmt_name]().format(_record(f"connect {label}: {secret} done"))
    assert bare not in out, f"{label} survived redaction in the {fmt_name} formatter"
    assert REDACTED in out or "***" in out
    # the surrounding text must still be there: a formatter that "hides" the
    # secret by not rendering the record at all is broken, not safe
    assert f"connect {label}:" in out, f"{fmt_name} dropped the message"
    assert "done" in out, f"{fmt_name} dropped the tail of the message"


@pytest.mark.unit
@pytest.mark.parametrize("fmt_name", sorted(FORMATTERS))
@pytest.mark.parametrize("label", sorted(SECRETS))
def test_formatter_redacts_a_secret_that_arrives_through_args(fmt_name, label):
    """A separate path: the secret is interpolated by getMessage()/super(),
    never present in the format string. Redacting only the raw message would
    miss it entirely -- and would also stop interpolating, so assert the
    message is still rendered, not just that the secret is absent."""
    secret = SECRETS[label]
    bare = secret.split("=", 1)[-1]
    out = FORMATTERS[fmt_name]().format(_record("connecting with %s (retry 1/3)", (secret,)))
    assert bare not in out, f"{label} passed through args unredacted in {fmt_name}"
    assert "connecting with" in out, f"{fmt_name} did not interpolate %args"
    assert "retry 1/3" in out, f"{fmt_name} did not interpolate %args"
    # the key invariant: the secret is gone because it was REDACTED, not
    # because it was never interpolated. Without this, returning record.msg
    # verbatim passes every absence assertion above.
    assert "***" in out, (
        f"{fmt_name} dropped the secret without redacting it -- the args were "
        "never interpolated, so the line is silently empty"
    )


@pytest.mark.unit
@pytest.mark.parametrize("fmt_name", sorted(FORMATTERS))
def test_formatter_redacts_a_traceback(fmt_name):
    out = FORMATTERS[fmt_name]().format(_raising_record())
    assert "p4ssw0rd" not in out, f"the traceback leaked a credential via {fmt_name}"


@pytest.mark.unit
def test_json_formatter_stays_valid_json_after_redacting():
    """Redaction runs before serialisation; a mangled payload would break
    every log shipper downstream."""
    out = JsonFormatter().format(_record(f"connect: {SECRETS['mongo uri']}"))
    payload = json.loads(out)
    assert payload["level"] == "INFO"
    assert payload["name"] == "ThunderBot"
    assert "p4ssw0rd" not in payload["msg"]
    # the mongo pattern substitutes plain *** rather than the REDACTED marker
    assert "***" in payload["msg"]


@pytest.mark.unit
def test_json_formatter_carries_the_traceback_as_a_field():
    out = json.loads(JsonFormatter().format(_raising_record()))
    assert "exc" in out
    assert "p4ssw0rd" not in out["exc"]


@pytest.mark.unit
def test_json_formatter_survives_a_non_ascii_message():
    """ensure_ascii=False is chosen on purpose: mojibake in bot.txt is a real
    support problem for a bot whose users are not all English."""
    out = json.loads(JsonFormatter().format(_record("影 emoji 😀")))
    assert out["msg"] == "影 emoji 😀"


@pytest.mark.unit
def test_json_formatter_redacts_non_string_messages():
    """logger.info("%s", obj) yields whatever __str__ gives; redaction is
    applied to the rendered message, never to record.msg directly."""
    out = json.loads(JsonFormatter().format(_record("value %s", (SECRETS["bot token"],))))
    assert SECRETS["bot token"] not in out["msg"]


@pytest.mark.unit
def test_redact_secrets_leaves_ordinary_text_alone():
    text = "Canonical record lookup failed (transport): server timeout"
    assert redact_secrets(text) == text


@pytest.mark.unit
def test_every_formatter_the_module_can_install_redacts():
    """Structural guard: whatever logger.py wires onto the file handler must
    redact, without this file having to enumerate the possibilities."""
    import inspect

    import Thunder.utils.logger as logmod

    installed = {
        name
        for name, obj in vars(logmod).items()
        if inspect.isclass(obj)
        and issubclass(obj, logging.Formatter)
        and obj is not logging.Formatter
    }
    assert installed, "found no formatter classes in logger.py"
    for name in sorted(installed):
        cls = getattr(logmod, name)
        out = cls().format(_record(f"connect: {SECRETS['mongo uri']}"))
        assert "p4ssw0rd" not in out, f"{name} does not redact"
