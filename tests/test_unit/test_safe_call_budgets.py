"""The RPC budget contract (H4a / H8) -- which shapes run unbounded.

The wall-clock budget is chosen from the callable's name. That is only safe if
the set of unbounded shapes is exactly the set of media calls actually routed
through tg_call, and if a wrapped callable still resolves to its real name.
Both properties are pinned here; the FloodWait caps themselves are covered by
test_safe_call.
"""

import asyncio
from functools import partial

import pytest
from pyrogram.errors import FloodWait

from Thunder.utils import safe_call
from Thunder.utils.safe_call import UNBOUNDED_SHAPES, tg_call


@pytest.mark.unit
def test_every_media_call_site_is_unbounded():
    """The real contract is not the set's contents but that no media call site
    is missing from it. The call sites are read out of the source rather than
    restating a literal: /log ships a 5 MiB tail and /shell ships arbitrary
    output, and a 30 s budget on either is a production failure."""
    import pathlib
    import re

    import Thunder

    root = pathlib.Path(Thunder.__file__).parent
    media_shapes = re.compile(
        r"tg_call\(\s*[\w.]*?(copy|reply_document|send_document|send_video|send_audio"
        r"|send_photo|send_cached_media|stream_media|download_media)\b"
    )
    found: set[str] = set()
    for path in root.rglob("*.py"):
        found |= {m.group(1) for m in media_shapes.finditer(path.read_text(encoding="utf-8"))}

    assert found, "scan found no media call sites -- the pattern is stale"
    assert found <= UNBOUNDED_SHAPES, f"media calls without a budget: {found - UNBOUNDED_SHAPES}"


@pytest.mark.unit
def test_lightweight_shapes_get_the_env_budget():
    async def reply_text():
        return None

    assert safe_call._default_timeout(reply_text) == safe_call._env_timeout()


@pytest.mark.unit
def test_reply_document_is_unbounded():
    """/log tails up to 5 MiB and /shell ships arbitrary output; both go out
    through reply_document, which must not inherit the 30 s budget."""

    async def reply_document(*args, **kwargs):
        return None

    assert safe_call._default_timeout(reply_document) is None


@pytest.mark.unit
def test_partial_wrapped_media_shape_keeps_its_budget():
    """functools.partial hides the callable behind .func; without unwrapping it
    a wrapped copy would look unnamed and inherit the 30s budget."""

    async def copy(*args, **kwargs):
        return None

    assert safe_call._call_name(partial(copy, chat_id=1)) == "copy"
    assert safe_call._default_timeout(partial(copy, chat_id=1)) is None


@pytest.mark.unit
async def test_partial_wrapped_media_shape_sleeps_the_media_cap(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("Thunder.utils.safe_call.asyncio.sleep", fake_sleep)

    async def copy(*args, **kwargs):
        raise FloodWait(value=90)

    with pytest.raises(FloodWait):
        await tg_call(partial(copy, chat_id=1), retries=1)
    assert slept == [90.0], "partial-wrapped media call lost the 600s ceiling"


@pytest.mark.unit
async def test_explicit_zero_opts_out_of_the_budget():
    """The documented escape hatch for a new media call site."""

    async def send_document(*args, **kwargs):
        await asyncio.sleep(0.2)
        return "ok"

    assert await tg_call(send_document, timeout=0, retries=0) == "ok"


@pytest.mark.unit
async def test_explicit_timeout_overrides_the_shape_default(monkeypatch):
    """A media shape given an explicit budget honours it -- the reverse
    direction also has to hold, or a caller could not tighten a call."""

    async def copy(*args, **kwargs):
        await asyncio.sleep(5)

    with pytest.raises(asyncio.TimeoutError):
        await tg_call(copy, timeout=0.05, retries=0)
