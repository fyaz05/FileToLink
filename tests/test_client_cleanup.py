"""Client cleanup must stop live clients and skip the rest.

Pyrogram's terminate() raises ConnectionError when is_initialized is falsy,
which used to surface as a traceback on every shutdown.
"""
import pytest

from Thunder.bot import multi_clients
from Thunder.bot.clients import cleanup_clients


class FakeClient:
    """Mirrors the two Pyrogram flags the guard depends on."""

    def __init__(self, initialized, connected=None):
        self.is_initialized = initialized
        self.is_connected = connected
        self.stop_calls = 0

    async def stop(self, block=True):
        self.stop_calls += 1
        self.is_initialized = False
        return self


@pytest.fixture(autouse=True)
def clear_clients():
    multi_clients.clear()
    yield
    multi_clients.clear()


async def test_skips_a_client_that_never_started():
    client = FakeClient(initialized=None)
    multi_clients[0] = client
    await cleanup_clients()
    assert client.stop_calls == 0


async def test_skips_an_already_terminated_client():
    client = FakeClient(initialized=False)
    multi_clients[0] = client
    await cleanup_clients()
    assert client.stop_calls == 0


async def test_stops_an_initialized_client():
    client = FakeClient(initialized=True)
    multi_clients[0] = client
    await cleanup_clients()
    assert client.stop_calls == 1


async def test_stops_only_the_initialized_clients():
    live = FakeClient(initialized=True)
    dead = FakeClient(initialized=False)
    fresh = FakeClient(initialized=None)
    multi_clients[0] = live
    multi_clients[1] = dead
    multi_clients[2] = fresh

    await cleanup_clients()

    assert live.stop_calls == 1
    assert dead.stop_calls == 0
    assert fresh.stop_calls == 0


async def test_a_failing_stop_does_not_prevent_the_rest():
    class Exploding(FakeClient):
        async def stop(self, block=True):
            raise RuntimeError("stop failed")

    bad = Exploding(initialized=True)
    good = FakeClient(initialized=True)
    multi_clients[0] = bad
    multi_clients[1] = good

    await cleanup_clients()

    assert good.stop_calls == 1, "a failure must not abort the loop"
