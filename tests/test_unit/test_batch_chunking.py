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


def link_for(filename: str, i: int) -> str:
    """What a link actually looks like: the name is percent-encoded in the URL."""
    return f"{BASE}{quote(filename)}{i}"


def _worst_case_message(chunk: list[str]) -> str:
    """Render exactly what goes on the wire, using the longer of the two forms.

    The same chunk_text is sent to the group AND DM-mirrored with a prefix that
    carries the chat title, so the DM form is the one that can bust the cap.
    """
    body = stream._batch_chunk_text(chunk)
    dm = MSG_DM_BATCH_PREFIX.format(chat_title=html.escape("A" * 128)) + "\n" + body
    return dm if telegram_len(dm) > telegram_len(body) else body


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
