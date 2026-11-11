"""Slot conservation and truncation behaviour of the download routes.

Runs a real aiohttp server on localhost and drives it with a real client, so
the assertions cover the path a user actually takes rather than a mock of it.
"""
import asyncio

import aiohttp
import pytest
from aiohttp import web

from Thunder.bot import multi_clients, work_loads
from Thunder.server import stream_routes as sr

FILE_SIZE = 64 * 1024
CHUNK = 16 * 1024


class FakeStreamer:
    """Stands in for ByteStreamer; declared size always matches what is sent
    unless a test asks for a short or failing transfer."""

    def __init__(self, mode="ok"):
        self.mode = mode

    async def get_file_info(self, message_id):
        if self.mode == "fail_info":
            raise RuntimeError("metadata fetch failed")
        return {
            "file_size": FILE_SIZE,
            "mime_type": "application/octet-stream",
            "file_unique_id": "abcdef123456",
        }

    async def stream_file(self, media_ref, offset=0, limit=None,
                          fallback_message_id=None, on_fallback_message=None):
        chunks = 2 if self.mode == "short" else FILE_SIZE // CHUNK
        for index in range(chunks):
            if self.mode == "fail_mid" and index == 2:
                raise RuntimeError("transport failed mid-stream")
            yield b"x" * CHUNK


@pytest.fixture
async def server(monkeypatch):
    """Localhost app serving the real routes with a controllable streamer."""
    monkeypatch.setattr(sr, "parse_media_request", lambda path, query: (1, "abcdef"))
    holder = {"streamer": FakeStreamer()}
    monkeypatch.setattr(sr, "get_streamer", lambda client_id: holder["streamer"])

    work_loads.clear()
    multi_clients.clear()
    work_loads[0] = 0

    app = web.Application()
    app.add_routes(sr.routes)
    runner = web.AppRunner(app, handler_cancellation=True, shutdown_timeout=5)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    yield f"http://127.0.0.1:{port}/1234567890abcdef", holder

    await runner.cleanup()


async def wait_for_release(timeout=8.0):
    async def poll():
        while work_loads.get(0, 0) != 0:
            await asyncio.sleep(0.02)
    await asyncio.wait_for(poll(), timeout)


async def test_completed_download_releases_slot(server):
    base, _ = server
    async with aiohttp.ClientSession() as session:
        async with session.get(base) as response:
            body = await response.read()
            assert response.status == 200
    assert len(body) == FILE_SIZE
    await wait_for_release()
    assert work_loads[0] == 0


async def test_head_releases_slot(server):
    base, _ = server
    async with aiohttp.ClientSession() as session:
        async with session.head(base) as response:
            assert response.status == 200
    await wait_for_release()
    assert work_loads[0] == 0


async def test_range_request_releases_slot(server):
    base, _ = server
    async with aiohttp.ClientSession() as session:
        async with session.get(base, headers={"Range": "bytes=100-199"}) as response:
            body = await response.read()
            assert response.status == 206
            assert response.headers["Content-Range"] == f"bytes 100-199/{FILE_SIZE}"
    assert len(body) == 100
    await wait_for_release()
    assert work_loads[0] == 0


async def test_client_disconnect_releases_slot(server):
    """handler_cancellation turns a disconnect into CancelledError, which
    except-Exception never sees; the slot must still come back."""
    base, _ = server
    async with aiohttp.ClientSession() as session:
        response = await session.get(base)
        received = 0
        async for chunk in response.content.iter_chunked(CHUNK):
            received += len(chunk)
            if received >= 2 * CHUNK:
                break
        response.close()
    await wait_for_release()
    assert work_loads[0] == 0


async def test_short_transfer_aborts_and_releases_slot(server):
    """Content-Length is already promised, so ending early must abort rather
    than leave the client waiting for bytes that never arrive."""
    base, holder = server
    holder["streamer"] = FakeStreamer("short")
    async with aiohttp.ClientSession() as session:
        with pytest.raises(aiohttp.ClientPayloadError):
            async with session.get(base) as response:
                await response.read()
    await wait_for_release()
    assert work_loads[0] == 0


async def test_mid_stream_failure_aborts_and_releases_slot(server):
    base, holder = server
    holder["streamer"] = FakeStreamer("fail_mid")
    async with aiohttp.ClientSession() as session:
        with pytest.raises(aiohttp.ClientPayloadError):
            async with session.get(base) as response:
                await response.read()
    await wait_for_release()
    assert work_loads[0] == 0


async def test_metadata_failure_returns_500_and_releases_slot(server):
    """Failure before the headers are sent can still become a real response."""
    base, holder = server
    holder["streamer"] = FakeStreamer("fail_info")
    async with aiohttp.ClientSession() as session:
        async with session.get(base) as response:
            assert response.status == 500
    await wait_for_release()
    assert work_loads[0] == 0


async def test_capacity_limit_does_not_touch_counter(server):
    base, _ = server
    work_loads[0] = sr.MAX_CONCURRENT_PER_CLIENT
    async with aiohttp.ClientSession() as session:
        async with session.get(base) as response:
            assert response.status == 503
    assert work_loads[0] == sr.MAX_CONCURRENT_PER_CLIENT
