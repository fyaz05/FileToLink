"""A rejected token must fail startup, not just warn.

A dead secondary client used to warn and boot anyway, easy to miss; a missing
client is never safe when it changes what is streamed.
"""
import asyncio

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
    """A secondary client whose token Telegram rejects."""

    def __init__(self, **kwargs):
        pass

    async def start(self):
        raise Unauthorized()


class ThrottledClient:
    """A secondary client that cannot reach Telegram yet."""

    def __init__(self, **kwargs):
        pass

    async def start(self):
        raise OSError("network unreachable")


class HealthyClient:
    """A secondary client that starts and looks fine to the checks."""

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
