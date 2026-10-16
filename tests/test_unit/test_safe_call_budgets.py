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

# Message methods that move a media payload. Spelled out as verb x noun rather
# than a prefix, because `reply_` also matches reply_text and `edit_` matches
# edit_reply_markup -- both of which are legitimately budgeted.
_MEDIA_NOUNS = (
    "document",
    "photo",
    "video",
    "audio",
    "animation",
    "voice",
    "video_note",
    "media_group",
    "paid_media",
)
_MESSAGE_MEDIA_SHAPES = {"copy"} | {
    f"{verb}_{noun}" for verb in ("reply", "edit") for noun in _MEDIA_NOUNS
}


@pytest.mark.unit
def test_every_media_call_site_is_unbounded():
    """The real contract is not the set's contents but that no media call site
    is missing from it. The call sites are read out of the source rather than
    restating a literal: /log ships a 5 MiB tail and /shell ships arbitrary
    output, and a 30 s budget on either is a production failure.

    The shape list is derived from pyrofork's own Client methods rather than
    retyped. It used to name nine shapes and omit send_animation,
    send_video_note, send_voice, send_paid_media and copy_message -- so
    adding one of those as a tg_call site would inherit the 30 s budget while
    this test stayed green.
    """
    import inspect
    import pathlib
    import re

    from pyrogram import Client
    from pyrogram.types import Message

    import Thunder

    root = pathlib.Path(Thunder.__file__).parent

    # every method that transfers a payload: the budget cannot apply to any of
    # them. Text and metadata shapes are excluded on purpose -- the API caps
    # them (4096 chars for a message), so a 30 s budget is correct there. The
    # contract being protected is about media, not about every send_* method.
    text_or_metadata = {
        "send_chat_action",
        "send_code",
        "send_contact",
        "send_dice",
        "send_game",
        "send_inline_bot_result",
        "send_location",
        "send_message",
        "send_paid_reaction",
        "send_reaction",
        "send_recovery_code",
        "send_todo",
        "send_venue",
        "send_web_page",
    }
    media_methods = {
        name
        for name in dir(Client)
        if name.startswith(("send_", "copy", "stream_", "download_", "reply_"))
        and name not in text_or_metadata
    }
    # Message carries the other half: Message.copy is the ingest forward and
    # Message.reply_document ships /log's 5 MiB tail and /shell's output.
    media_methods |= {name for name in dir(Message) if name in _MESSAGE_MEDIA_SHAPES}
    assert len(media_methods) > 20, (
        f"only found {len(media_methods)} transfer methods -- the derivation is broken"
    )
    # the two shapes actually wired unbounded, so a regression that moved the
    # call off them cannot pass silently
    assert {"copy", "reply_document"} <= media_methods, (
        f"the live call sites use Message.copy and Message.reply_document; "
        f"derived set has {sorted(media_methods & {'copy', 'reply_document'})}"
    )
    # the scan and the derived set must agree, so a shape can never be quietly
    # missing from the regex while the comparison still passes
    scanned = "|".join(sorted(media_methods))
    media_shapes = re.compile(rf"tg_call\(\s*[\w.]*?({scanned})\b")
    found: set[str] = set()
    for path in root.rglob("*.py"):
        found |= {m.group(1) for m in media_shapes.finditer(path.read_text(encoding="utf-8"))}

    assert found, "scan found no media call sites -- the pattern is stale"
    assert found <= UNBOUNDED_SHAPES, f"media calls without a budget: {found - UNBOUNDED_SHAPES}"
    assert inspect.isfunction(safe_call._env_timeout)


@pytest.mark.unit
def test_lightweight_shapes_get_the_env_budget():
    """Assert against Var, not against _env_timeout() itself.

    _default_timeout returns _env_timeout() by construction, so comparing the
    two asserted f(x) == f() and pinned nothing: hardcoding _env_timeout to
    DEFAULT_RPC_TIMEOUT_SECONDS and deleting the Var.TG_RPC_TIMEOUT_SECONDS read
    left every test green, and the documented env knob had no coverage at all.
    """
    from Thunder.vars import Var

    async def reply_text():
        return None

    expected = float(Var.TG_RPC_TIMEOUT_SECONDS)
    assert expected > 0, "TG_RPC_TIMEOUT_SECONDS is not a usable number"
    assert safe_call._env_timeout() == expected, (
        f"the RPC budget ignores TG_RPC_TIMEOUT_SECONDS ({Var.TG_RPC_TIMEOUT_SECONDS})"
    )
    assert safe_call._default_timeout(reply_text) == expected


@pytest.mark.unit
def test_the_env_budget_knob_is_read_not_hardcoded():
    """Distinct from the test above: this one reads the source, so it fails even
    if the value happens to match today."""
    import inspect

    src = inspect.getsource(safe_call._env_timeout)
    assert "TG_RPC_TIMEOUT_SECONDS" in src, "_env_timeout no longer reads the configured timeout"


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
