"""A client that fails to start must not be left running.

start_client discarded the object when start() raised, so a token that
connected and then failed left an MTProto session behind on every boot. The
fix has to survive the case where the Client constructor itself raised, which
is why the binding is initialised before the try.
"""

import pytest

from Thunder.bot import clients as clients_mod
from Thunder.utils import safe_call


async def _tg_call(fn, *args, **kwargs):
    """Stand-in for the real tg_call, which consumes its own kwargs."""
    kwargs.pop("timeout", None)
    kwargs.pop("retries", None)
    return await fn(*args, **kwargs)


def _arm(monkeypatch, client_factory):
    monkeypatch.setattr(clients_mod, "Client", client_factory)
    monkeypatch.setattr(clients_mod, "tg_call", _tg_call)
    monkeypatch.setattr(safe_call, "tg_call", _tg_call)
    monkeypatch.setattr(clients_mod.TokenParser, "parse_from_env", lambda self: {7: "tok"})


@pytest.mark.unit
async def test_failed_start_stops_the_partially_started_client(monkeypatch):
    stopped: list[int] = []

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            raise RuntimeError("auth key rejected")

        async def stop(self):
            stopped.append(1)

    _arm(monkeypatch, _Client)

    await clients_mod.initialize_clients()

    assert stopped == [1], "the partially started client was never stopped"
    assert 7 not in clients_mod.multi_clients


@pytest.mark.unit
async def test_constructor_failure_does_not_raise_unbound(monkeypatch):
    """If Client() itself raises there is nothing to stop -- and the handler
    must swallow that instead of dying on an unbound name."""

    def _boom(**kwargs):
        raise ValueError("bad token string")

    _arm(monkeypatch, _boom)

    await clients_mod.initialize_clients()  # must not raise

    assert 7 not in clients_mod.multi_clients


@pytest.mark.unit
async def test_successful_start_is_still_registered(monkeypatch):
    started: list[int] = []
    stopped: list[int] = []

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            started.append(1)

        async def stop(self):
            stopped.append(1)

    _arm(monkeypatch, _Client)

    try:
        await clients_mod.initialize_clients()

        assert started == [1]
        assert stopped == [], "a healthy client must not be stopped"
        assert isinstance(clients_mod.multi_clients.get(7), _Client)
    finally:
        # the registry is module-level: leave it as we found it
        clients_mod.multi_clients.pop(7, None)
        clients_mod.work_loads.pop(7, None)
