"""Transient Telegram failures must NOT surface as FileNotFound (self-heal deletes records)."""

from types import SimpleNamespace

import pytest
from pyrogram.errors import FloodWait

from Thunder.server.exceptions import FileNotFound, TelegramUnavailable
from Thunder.utils.custom_dl import ByteStreamer


@pytest.mark.unit
async def test_transport_error_maps_to_unavailable():
    class _C:
        async def get_messages(self, *a, **k):
            raise TimeoutError("upstream hung")

    with pytest.raises(TelegramUnavailable):
        await ByteStreamer(_C()).get_message(1)


@pytest.mark.unit
async def test_genuine_absence_maps_to_file_not_found():
    class _C:
        async def get_messages(self, *a, **k):
            return SimpleNamespace(media=None, id=1)

    with pytest.raises(FileNotFound):
        await ByteStreamer(_C()).get_message(1)


@pytest.mark.unit
async def test_stream_file_floodwait_midstream_resumes():
    """A FloodWait mid-stream resumes from the consumer's position (no
    re-sent bytes) instead of surfacing as a failure."""

    class _C:
        def __init__(self):
            self.calls = 0

        async def stream_media(self, target, offset=0, limit=0):
            self.calls += 1
            if self.calls == 1:
                yield b"x"
                raise FloodWait(value=0)  # sleep(0): hermetic, exercises resume math
            yield b"y"

    streamer = ByteStreamer(_C())
    chunks = [chunk async for chunk in streamer.stream_file(SimpleNamespace(), offset=0)]
    assert chunks == [b"x", b"y"]


@pytest.mark.unit
async def test_stream_file_sustained_floodwait_caps_at_60s():
    """A FloodWait exceeding the 60s total cap maps to TelegramUnavailable
    WITHOUT sleeping (hermetic: the cap trips before any sleep)."""

    class _C:
        async def stream_media(self, target, offset=0, limit=0):
            raise FloodWait(value=61)
            yield b"never"  # noqa: unreachable -- keeps the generator shape

    streamer = ByteStreamer(_C())
    with pytest.raises(TelegramUnavailable, match="Sustained Telegram flood"):
        [chunk async for chunk in streamer.stream_file(SimpleNamespace(), offset=0)]
