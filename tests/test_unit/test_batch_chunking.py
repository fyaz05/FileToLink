"""Batch link chunks must fit inside one Telegram message.

The bug: chunks were cut by *count* (``LINK_CHUNK_SIZE = 20``), but each link
embeds the percent-encoded file name -- one CJK glyph becomes nine characters
and one emoji likewise. A 20-link chunk of CJK-named files rendered to ~12000
UTF-16 units against a 4096 cap. ``reply_safe`` raised, the handler logged the
error and moved on, and the user silently lost every link in that chunk -- in a
private chat with no notice at all.

These tests pin the property that matters: no rendered message Telegram would
reject, and no link dropped from the output.
"""

import html
import inspect
from urllib.parse import quote

import pytest

from Thunder.bot.plugins import stream
from Thunder.utils.messages import (
    MSG_BATCH_LINKS_READY,
    MSG_DM_BATCH_PREFIX,
    TELEGRAM_MAX_UNITS,
    telegram_len,
)

BASE = "https://thunder.example/f/0123456789abcdef0123456789abcdef/"

# names long enough that a count-based chunk of 20 cannot fit
LONG_NAMES = [
    ("ascii", "a" * 200),
    ("cjk", "影" * 60),
    ("emoji", "\U0001f600" * 60),
    ("mixed", "mixed 混合 \U0001f600 name" * 12),
    ("html-hostile", "<script>&" + "'" + '"' + " " * 200),
]

# Group titles are legal up to 128 chars, and html.escape expands some single
# characters to six ("&#x27;"). A title of plain "A"s -- which this file used
# to model -- escapes to 128 units and cannot occur; it made a 4252-unit DM
# look like it fit, and the overflow shipped green.
HOSTILE_TITLES = ["'" * 128, '"' * 128, "&" * 128, "<" * 128, ">" * 128, "A" * 128]


def link_for(filename: str, i: int) -> str:
    """What a link actually looks like: the name is percent-encoded in the URL."""
    return f"{BASE}{quote(filename)}{i}"


def _dm_message(chunk: list[str], title: str) -> str:
    return MSG_DM_BATCH_PREFIX.format(chat_title=title) + "\n" + stream._batch_chunk_text(chunk)


def _worst_case_message(chunk: list[str]) -> str:
    """Render exactly what goes on the wire, using the longer of the two forms.

    The same chunk_text is sent to the group AND DM-mirrored with a prefix that
    carries the chat title, so the DM form is the one that can bust the cap.
    Checked against every hostile title, not one convenient sample.
    """
    body = stream._batch_chunk_text(chunk)
    worst = max(_dm_message(chunk, html.escape(t)) for t in HOSTILE_TITLES)
    return worst if telegram_len(worst) > telegram_len(body) else body


@pytest.mark.unit
@pytest.mark.parametrize(("name", "filename"), LONG_NAMES)
def test_count_chunking_would_bust_the_cap(name, filename):
    """Pin the bug itself: the old count-based chunk of 20 links does not fit.

    If this ever stops holding the chunking has only got *more* conservative,
    which is fine -- but the regression it guards must stay visible.
    """
    old_chunk = [link_for(filename, i) for i in range(20)]
    width = telegram_len(_worst_case_message(old_chunk))
    assert width > TELEGRAM_MAX_UNITS, (
        f"{name}: the old 20-link chunk is only {width} units, so this guard no "
        "longer describes the real failure"
    )


@pytest.mark.unit
@pytest.mark.parametrize(("name", "filename"), LONG_NAMES)
def test_every_rendered_chunk_fits_the_cap(name, filename):
    links = [link_for(filename, i) for i in range(20)]
    chunks = stream._batch_chunks(links)
    assert len(chunks) > 1, f"{name}: expected the 20 links to need more than one chunk"
    for chunk in chunks:
        width = telegram_len(_worst_case_message(chunk))
        assert width <= TELEGRAM_MAX_UNITS, f"{name}: a chunk is {width} units"


@pytest.mark.unit
def test_chunking_never_drops_or_reorders_a_link():
    links = [link_for(f"file-{i}.mp4", "") for i in range(200)]
    chunks = stream._batch_chunks(links)
    assert [link for chunk in chunks for link in chunk] == links, (
        "a link was dropped or reordered -- the user would get the wrong file"
    )


@pytest.mark.unit
def test_short_names_stay_in_one_chunk():
    """Don't split needlessly: every extra message is another FloodWait."""
    links = [link_for(f"f{i}.mp4", "") for i in range(20)]
    assert stream._batch_chunks(links) == [links]


@pytest.mark.unit
def test_no_links_yields_no_chunks():
    assert stream._batch_chunks([]) == []


@pytest.mark.unit
def test_one_link_wider_than_the_cap_still_gets_its_own_chunk():
    """Must not loop forever or drop it -- Telegram rejects it, which the
    handler then reports, rather than it vanishing silently."""
    links = [link_for("z" * 9000, "")]
    assert stream._batch_chunks(links) == [links]


@pytest.mark.unit
def test_escaped_width_is_what_is_measured():
    """A link full of & < > grows again when escaped for the <code> block.
    Measuring the raw link would let the escaped message over the cap."""
    raw = BASE + "&" * 100
    escaped = html.escape(raw)
    assert len(escaped) > len(raw)
    for chunk in stream._batch_chunks([raw] * 20):
        assert telegram_len(_worst_case_message(chunk)) <= TELEGRAM_MAX_UNITS


@pytest.mark.unit
def test_header_count_matches_the_links_actually_in_the_chunk():
    links = [link_for("影" * 60, i) for i in range(9)]
    chunks = stream._batch_chunks(links)
    assert len(chunks) > 1
    for chunk in chunks:
        assert MSG_BATCH_LINKS_READY.format(count=len(chunk)) in stream._batch_chunk_text(chunk), (
            "the header says a different count than the chunk holds"
        )


@pytest.mark.unit
def test_chunk_budget_leaves_headroom_under_the_cap():
    assert stream._LINK_CHUNK_UNITS < TELEGRAM_MAX_UNITS
    assert stream._LINK_CHUNK_UNITS > TELEGRAM_MAX_UNITS // 2, (
        "budget collapsed to a handful of links per message"
    )


@pytest.mark.unit
@pytest.mark.parametrize("title_char", ["'", '"', "&", "<", ">", "A"])
def test_budget_actually_covers_the_worst_case_dm_prefix(title_char):
    """The structural guard the first version lacked.

    Computes the real overhead rather than trusting a hand-written constant: a
    flat reserve below this number is a bug, whatever the surrounding comment
    claims. This is what a flat 600 failed -- html.escape turns one apostrophe
    into six, so a legal 128-char title costs up to 768 units, not 128.
    """
    overhead = telegram_len(_dm_message([], title_char * 128))
    reserve = TELEGRAM_MAX_UNITS - stream._LINK_CHUNK_UNITS
    assert reserve >= overhead, (
        f"reserve is {reserve} units but the DM prefix alone costs {overhead} "
        f"with a title of {title_char!r}*128 -- Telegram will reject the message"
    )


@pytest.mark.unit
def test_a_full_batch_fits_for_every_hostile_title():
    """MAX_BATCH_FILES files, the longest realistic names, every title that can
    legally appear. The case that actually shipped broken."""
    links = [link_for("a" * 200, i) for i in range(50)]
    chunks = stream._batch_chunks(links)
    assert len(chunks) > 1
    for chunk in chunks:
        for title in HOSTILE_TITLES:
            rendered = _dm_message(chunk, html.escape(title))
            assert telegram_len(rendered) <= TELEGRAM_MAX_UNITS, (
                f"a {telegram_len(rendered)}-unit DM exceeds the cap "
                f"(title {title[0]!r}*128, chunk of {len(chunk)})"
            )


@pytest.mark.unit
def test_chat_title_is_clamped_before_escaping():
    """Truncate the RAW title, not the escaped one: slicing escaped text can
    cut &#x27; in half and render literal garbage."""
    assert stream._MAX_TITLE_CHARS == 128, "Telegram caps group titles at 128"
    assert (
        len(html.escape("'" * stream._MAX_TITLE_CHARS))
        <= stream._MAX_TITLE_CHARS * stream._WORST_ESCAPE_EXPANSION
    )


# --- delivery: a chunk that fails to send must be REPORTED ------------------
#
# The commit that introduced MSG_ERROR_BATCH_CHUNK_FAILED claimed it as the
# point of the change. Deleting the whole reporting block left all 17 tests
# green -- there was no test for it at all. These drive _deliver_chunks
# directly, which is why it was extracted out of process_batch.


def _msg(chat_type, from_user=True, title="Group"):
    from types import SimpleNamespace

    return SimpleNamespace(
        chat=SimpleNamespace(type=chat_type, title=title),
        from_user=SimpleNamespace(id=7) if from_user else None,
    )


def _wire(monkeypatch, reply=None, send=None):
    async def _maybe(fn, *args):
        # callbacks may be sync (list.append) or async (raising stubs)
        out = fn(*args)
        if inspect.isawaitable(out):
            return await out
        return out

    async def fake_reply(msg, text=None, **kwargs):
        return await _maybe(reply, text) if reply else None

    async def fake_send(bot, user_id, text=None, **kwargs):
        return await _maybe(send, text, kwargs) if send else None

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(stream, "reply_safe", fake_reply)
    monkeypatch.setattr(stream, "send_safe", fake_send)
    monkeypatch.setattr(stream.asyncio, "sleep", no_sleep)


@pytest.mark.unit
async def test_all_chunks_delivered_reports_no_failures(monkeypatch):
    sent = []
    _wire(monkeypatch, reply=sent.append, send=lambda t, k: sent.append(t))

    links = [link_for(f"f{i}.mp4", "") for i in range(10)]
    chat_failed, dm_failed, total = await stream._deliver_chunks(
        None, _msg(stream.enums.ChatType.GROUP), links
    )

    assert (chat_failed, dm_failed, total) == (0, 0, 1)
    assert len(sent) == 2, "expected the group reply and the DM mirror"


@pytest.mark.unit
async def test_a_failed_group_chunk_is_counted(monkeypatch):
    async def boom(text):
        raise RuntimeError("MESSAGE_TOO_LONG")

    _wire(monkeypatch, reply=boom)

    links = [link_for("a" * 200, i) for i in range(50)]
    chat_failed, dm_failed, total = await stream._deliver_chunks(
        None, _msg(stream.enums.ChatType.PRIVATE), links
    )

    assert chat_failed == total > 0, "every group send failed but none was counted"
    assert dm_failed == 0, "a private chat has no DM mirror"


@pytest.mark.unit
async def test_a_failed_dm_chunk_is_counted_separately(monkeypatch):
    async def boom(text, kwargs):
        raise RuntimeError("bot was blocked by the user")

    _wire(monkeypatch, send=boom)

    links = [link_for(f"f{i}.mp4", "") for i in range(10)]
    chat_failed, dm_failed, total = await stream._deliver_chunks(
        None, _msg(stream.enums.ChatType.GROUP), links
    )

    assert (chat_failed, dm_failed, total) == (0, 1, 1)


@pytest.mark.unit
async def test_one_bad_chunk_does_not_stop_the_rest(monkeypatch):
    """One oversized chunk must not cost the user every other link."""
    attempts = []
    delivered = []

    async def fail_second(text):
        attempts.append(text)
        if len(attempts) == 2:
            raise RuntimeError("transient")
        delivered.append(text)

    _wire(monkeypatch, reply=fail_second)

    links = [link_for("a" * 200, i) for i in range(90)]
    chat_failed, _, total = await stream._deliver_chunks(
        None, _msg(stream.enums.ChatType.PRIVATE), links
    )

    assert total > 2, "need several chunks to tell continue from break"
    assert chat_failed == 1, f"exactly one chunk should have failed, got {chat_failed}"
    assert len(attempts) == total, (
        f"delivery stopped after the failing chunk: {len(attempts)} attempts for {total} chunks"
    )
    assert len(delivered) == total - 1


@pytest.mark.unit
async def test_no_dm_mirror_without_a_sender(monkeypatch):
    """Anonymous admin / channel post: no from_user, so no DM attempt and no
    AttributeError."""
    dm_calls = []

    async def note(text, kwargs):
        dm_calls.append(text)

    _wire(monkeypatch, send=note)

    _chat_failed, dm_failed, _total = await stream._deliver_chunks(
        None, _msg(stream.enums.ChatType.GROUP, from_user=False), [link_for("a.mp4", "")]
    )

    assert not dm_calls, "attempted a DM with no sender to send it to"
    assert dm_failed == 0, (
        "a senderless message was counted as a DM delivery failure, so an "
        "anonymous admin is told to start a private chat"
    )


@pytest.mark.unit
async def test_delivery_sends_html_parsed(monkeypatch):
    """The DM prefix carries <b>, so a plain-text send would show the tag."""
    seen = {}

    async def note(text, kwargs):
        seen.update(kwargs)

    _wire(monkeypatch, send=note)

    await stream._deliver_chunks(None, _msg(stream.enums.ChatType.GROUP), [link_for("a.mp4", "")])

    assert seen.get("parse_mode") == stream.enums.ParseMode.HTML


@pytest.mark.unit
async def test_the_dm_mirror_escapes_the_chat_title(monkeypatch):
    """The title is user-controlled group data rendered inside an HTML message."""
    sent = []

    async def note(text, kwargs):
        sent.append(text)

    _wire(monkeypatch, send=note)

    await stream._deliver_chunks(
        None,
        _msg(stream.enums.ChatType.GROUP, title="<script>alert(1)</script>"),
        [link_for("a.mp4", "")],
    )

    assert sent, "no DM was sent"
    assert "<script>" not in sent[0], "the group title was not escaped"


@pytest.mark.unit
async def test_an_over_long_chat_title_is_clamped_to_128_chars(monkeypatch):
    """Belt and braces: Telegram caps titles at 128 chars so this cannot happen
    today, but the budget is sized from that limit and a longer title must not
    silently push the DM over the cap.

    Pinned by observable truncation rather than by size, because an over-long
    title made of plain letters escapes to no more units than the 128-char
    worst case the reserve already covers.
    """
    sent = []

    async def note(text, kwargs):
        sent.append(text)

    _wire(monkeypatch, send=note)

    marker = "BEYOND_THE_LIMIT"
    await stream._deliver_chunks(
        None,
        _msg(stream.enums.ChatType.GROUP, title="A" * 130 + marker),
        [link_for("a.mp4", "")],
    )

    assert sent, "no DM was sent"
    assert marker not in sent[0], "the title was not clamped to 128 characters before being escaped"


@pytest.mark.unit
async def test_a_full_batch_of_long_names_survives_the_worst_legal_title(monkeypatch):
    """The real guarantee: 50 long-named files, DM'd into a group whose title is
    128 apostrophes -- the worst title Telegram will actually accept."""
    sent = []

    async def note(text, kwargs):
        sent.append(text)

    _wire(monkeypatch, send=note)

    links = [link_for("a" * 200, i) for i in range(50)]
    await stream._deliver_chunks(None, _msg(stream.enums.ChatType.GROUP, title="'" * 128), links)

    assert sent, "no DM was sent"
    for message in sent:
        assert telegram_len(message) <= TELEGRAM_MAX_UNITS, (
            f"a {telegram_len(message)}-unit DM exceeds the cap"
        )
