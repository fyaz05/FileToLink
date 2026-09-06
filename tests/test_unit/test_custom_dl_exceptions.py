"""Transient Telegram failures must NOT surface as FileNotFound (self-heal deletes records)."""

from types import SimpleNamespace

import pytest

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
