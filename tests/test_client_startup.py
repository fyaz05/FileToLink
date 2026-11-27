"""Rejected secondary tokens fail startup; transient failures only warn."""
import asyncio

import pytest
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import Unauthorized
from pyrogram.types import ChatMember, ChatPrivileges, User

from Thunder.bot import clients as clients_module
from Thunder.utils import preflight
from Thunder.utils.preflight import WARN


def admin_member():
    return ChatMember(
        status=ChatMemberStatus.ADMINISTRATOR,
        privileges=ChatPrivileges(can_post_messages=True, can_delete_messages=True))


class RejectedClient:

    def __init__(self, **kwargs):
        pass

    async def start(self):
        raise Unauthorized()


class ThrottledClient:

    def __init__(self, **kwargs):
        pass

    async def start(self):
        raise OSError("network unreachable")


class HealthyClient:

    def __init__(self, **kwargs):
        pass

    async def start(self):
        return None

    async def stop(self, block=True):
        return None

    async def get_me(self):
        return User(id=123456, first_name="Secondary", username="secondary_bot", is_bot=True)

    async def get_chat_member(self, chat_id, user_id):
        return admin_member()

    async def get_messages(self, chat_id, message_ids=None):
        return None


class FakeParser:
    def __init__(self, tokens):
        self._tokens = tokens
        self.warnings = []

    def parse_from_env(self):
        return self._tokens


async def instant_sleep(_seconds):
    return None


def install(monkeypatch, client_cls, tokens):
    report = preflight.Report()
    monkeypatch.setattr(clients_module, "Client", client_cls)
    monkeypatch.setattr(clients_module, "TokenParser", lambda: FakeParser(tokens))
    monkeypatch.setattr(clients_module.Var, "MULTI_CLIENT", False)
    monkeypatch.setattr(asyncio, "sleep", instant_sleep)
    return report


async def test_a_rejected_secondary_token_is_a_failure(monkeypatch):
    report = install(monkeypatch, RejectedClient, {1: ("MULTI_TOKEN1", "123456:bad")})
    await clients_module.initialize_clients(report)
    assert report.failures, "a rejected token must be flagged"
    _status, text = report.failures[0]
    assert "MULTI_TOKEN1" in text
    assert "rejected" in text
    assert 1 not in clients_module.multi_clients


async def test_a_transient_start_error_stays_a_warning(monkeypatch):
    report = install(monkeypatch, ThrottledClient, {1: ("MULTI_TOKEN1", "123456:ok")})
    await clients_module.initialize_clients(report)
    assert not report.failures
    assert any(status == WARN for status, _text in report.warnings)


async def test_a_started_secondary_is_kept(monkeypatch):
    report = install(monkeypatch, HealthyClient, {1: ("MULTI_TOKEN1", "123456:ok")})
    await clients_module.initialize_clients(report)
    assert not report.failures
    assert clients_module.multi_clients.get(1) is not None
    assert clients_module.Var.MULTI_CLIENT is True


async def test_a_cancelled_secondary_check_remains_visible_to_common_cleanup(monkeypatch):
    class BlockingHealthyClient(HealthyClient):
        is_initialized = True
        instances = []

        def __init__(self, **kwargs):
            self.stopped = False
            self.instances.append(self)

        async def stop(self, block=True):
            self.stopped = True
            self.is_initialized = False

    check_started = asyncio.Event()

    async def blocking_check(*args, **kwargs):
        check_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(clients_module, "check_secondary_client", blocking_check)
    install(monkeypatch, BlockingHealthyClient, {1: ("MULTI_TOKEN1", "123456:ok")})
    task = asyncio.create_task(clients_module.initialize_clients(preflight.Report()))
    check_task = asyncio.create_task(check_started.wait())
    done, _pending = await asyncio.wait(
        {task, check_task}, timeout=1, return_when=asyncio.FIRST_COMPLETED
    )
    if task in done:
        check_task.cancel()
        await asyncio.gather(check_task, return_exceptions=True)
        await task
        raise AssertionError("initialize_clients finished before the secondary check started")
    if check_task not in done:
        task.cancel()
        check_task.cancel()
        await asyncio.gather(task, check_task, return_exceptions=True)
        raise AssertionError("secondary check did not start within one second")
    check_task.cancel()
    await asyncio.gather(check_task, return_exceptions=True)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    client = BlockingHealthyClient.instances[0]
    assert clients_module.cleanup_registry.get(1) is client
    assert 1 not in clients_module.multi_clients
    await clients_module.cleanup_clients()
    assert client.stopped
    assert 1 not in clients_module.cleanup_registry


async def test_a_failed_start_that_cannot_stop_aborts_and_remains_cleanable(monkeypatch):
    class FailedStartStopClient:
        is_initialized = False
        is_connected = True
        instances = []

        def __init__(self, **kwargs):
            self.stop_calls = 0
            self.instances.append(self)

        async def start(self):
            raise OSError("network unreachable")

        async def stop(self, block=True):
            self.stop_calls += 1
            if self.stop_calls == 1:
                raise RuntimeError("stop failed")
            self.is_connected = False

    install(monkeypatch, FailedStartStopClient, {1: ("MULTI_TOKEN1", "123456:ok")})
    with pytest.raises(RuntimeError, match="could not be started or stopped"):
        await clients_module.initialize_clients(preflight.Report())

    client = FailedStartStopClient.instances[0]
    assert 1 not in clients_module.multi_clients
    assert clients_module.cleanup_registry.get(1) is client
    await clients_module.cleanup_clients()
    assert client.stop_calls == 2
    assert not client.is_connected
    assert 1 not in clients_module.cleanup_registry


async def test_a_cancelled_failed_client_stop_keeps_it_available_for_cleanup(monkeypatch):
    class CancelStopClient(HealthyClient):
        is_initialized = True
        is_connected = True
        instances = []

        def __init__(self, **kwargs):
            self.stop_calls = 0
            self.instances.append(self)

        async def stop(self, block=True):
            self.stop_calls += 1
            if self.stop_calls == 1:
                raise asyncio.CancelledError
            self.is_initialized = False
            self.is_connected = False

    async def failed_check(*args, **kwargs):
        return [(preflight.FAIL, "unusable")]

    monkeypatch.setattr(clients_module, "check_secondary_client", failed_check)
    install(monkeypatch, CancelStopClient, {1: ("MULTI_TOKEN1", "123456:ok")})
    with pytest.raises(asyncio.CancelledError):
        await clients_module.initialize_clients(preflight.Report())

    client = CancelStopClient.instances[0]
    assert 1 not in clients_module.multi_clients
    assert clients_module.cleanup_registry.get(1) is client
    await clients_module.cleanup_clients()
    assert client.stop_calls == 2
    assert not client.is_initialized and not client.is_connected
    assert 1 not in clients_module.cleanup_registry


async def test_a_rejected_client_is_removed_from_routing_when_stop_raises(monkeypatch):
    class Rejected:
        is_initialized = True
        is_connected = True
        instances = []

        def __init__(self, **kwargs):
            self.instances.append(self)

        async def start(self):
            return None

        async def stop(self, block=True):
            raise RuntimeError("stop failed")

    async def failed_check(*args, **kwargs):
        return [(preflight.FAIL, "unusable")]

    monkeypatch.setattr(clients_module, "check_secondary_client", failed_check)
    install(monkeypatch, Rejected, {1: ("MULTI_TOKEN1", "123456:ok")})
    with pytest.raises(RuntimeError, match="remained connected"):
        await clients_module.initialize_clients(preflight.Report())

    client = Rejected.instances[0]
    assert 1 not in clients_module.multi_clients
    assert 1 not in clients_module.work_loads
    assert clients_module.cleanup_registry.get(1) is client
    assert 1 not in clients_module.client_keys
    assert 1 not in clients_module.client_bot_ids


async def test_no_secondary_tokens_reset_the_multi_client_flag(monkeypatch):
    install(monkeypatch, HealthyClient, {})
    clients_module.Var.MULTI_CLIENT = True
    await clients_module.initialize_clients(preflight.Report())
    assert clients_module.Var.MULTI_CLIENT is False


async def test_keyboard_interrupt_during_rejected_client_stop_keeps_cleanup_visibility(monkeypatch):
    class Interrupted:
        is_initialized = True
        is_connected = True
        instances = []

        def __init__(self, **kwargs):
            self.instances.append(self)

        async def start(self):
            return None

        async def stop(self, block=True):
            raise KeyboardInterrupt

    async def failed_check(*args, **kwargs):
        return [(preflight.FAIL, "unusable")]

    monkeypatch.setattr(clients_module, "check_secondary_client", failed_check)
    install(monkeypatch, Interrupted, {1: ("MULTI_TOKEN1", "123456:ok")})
    with pytest.raises(KeyboardInterrupt):
        await clients_module.initialize_clients(preflight.Report())

    client = Interrupted.instances[0]
    assert 1 not in clients_module.multi_clients
    assert clients_module.cleanup_registry.get(1) is client
