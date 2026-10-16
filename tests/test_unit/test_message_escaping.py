"""Links and file names must never reach a message as raw markup.

The client defaults to ParseMode.DEFAULT, which parses Markdown *and* HTML, and
these messages are sent with explicit HTML. Link values can come from the
shortener, i.e. from a third party, and file names come from users. Escaping is
therefore a security control (M7), not cosmetic -- and the escaping sites were
introduced piecemeal, so each one is pinned here.
"""

import pytest

from Thunder.bot.plugins import stream
from Thunder.utils.bot_utils import format_link_message, quote_media_name


def _links(**overrides) -> dict:
    base = {
        "online_link": "https://s.example/a?x=1&y=2",
        "stream_link": "https://bot.example/f/abc/name.mkv",
        "media_name": "clip.mkv",
        "media_size": 1024,
    }
    base.update(overrides)
    return base


@pytest.mark.unit
async def test_send_channel_links_escapes_links(monkeypatch):
    sent: list[dict] = []

    async def _send(bot, chat_id, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(stream, "send_safe", _send)
    await stream.send_channel_links(_links(), source_info="& <Channel>", source_id=-100123)

    text = sent[0]["text"]
    assert "&amp;" in text, "the shortener's & reached the message unescaped"
    assert "<Channel>" not in text, "the channel title reached the message unescaped"
    assert "&lt;Channel&gt;" in text


@pytest.mark.unit
async def test_send_channel_links_escapes_a_hostile_shortener(monkeypatch):
    """A shortener that returns markup must not be able to inject into the
    bot's own message."""
    sent: list[dict] = []

    async def _send(bot, chat_id, **kwargs):
        sent.append(kwargs)

    hostile = _links(
        online_link="https://evil.example/<b>INJECTED</b>",
        stream_link='https://evil.example/"><script>alert(1)</script>',
    )
    monkeypatch.setattr(stream, "send_safe", _send)
    await stream.send_channel_links(hostile, source_info="chan", source_id=-1)

    text = sent[0]["text"]
    assert "<b>INJECTED</b>" not in text
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


@pytest.mark.unit
async def test_send_channel_links_escapes_a_hostile_file_name(monkeypatch):
    sent: list[dict] = []

    async def _send(bot, chat_id, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(stream, "send_safe", _send)
    await stream.send_channel_links(
        _links(stream_link="https://b.example/<i>INJECTED</i>"),
        source_info="chan",
        source_id=-1,
    )
    assert "<i>INJECTED</i>" not in sent[0]["text"]


@pytest.mark.unit
def test_format_link_message_escapes_name_and_links():
    text = format_link_message(_links(media_name='<img src=x onerror="alert(1)">'))

    assert "<img" not in text
    assert "&lt;img" in text
    assert "&amp;" in text, "the shortener link was not escaped"


@pytest.mark.unit
def test_quote_media_name_neutralises_path_and_markup():
    quoted = quote_media_name("../../etc/<script>.mkv")
    assert "/" not in quoted
    assert "<" not in quoted and ">" not in quoted


@pytest.mark.unit
def test_batch_chunk_escapes_links():
    """Batch DM chunks put links inside a <code> block; the same rule applies.
    The template carries its own <b> markup, so the injected payload gets a
    unique marker to be asserted on."""
    text = stream._batch_chunk_text(
        ["https://s.example/<b>INJECTED</b>", "https://s.example/a?x=1&y=2"]
    )
    assert "<b>INJECTED</b>" not in text, "a hostile link reached the batch message"
    assert "&lt;b&gt;INJECTED&lt;/b&gt;" in text
    assert "&amp;" in text
    assert text.count("<code>") == 1
