"""The live checks, against fake clients built from real ``pyrogram.types``."""
import pytest
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import ChatAdminRequired, FloodWait, PeerIdInvalid, UserNotParticipant
from pyrogram.types import ChatMember, ChatPrivileges, User
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

from Thunder.utils import preflight

CHANNEL = -1001234567890


class FakeChat:
    """Carries the type name the check matches on, not an enum member."""

    def __init__(self, type_name):
        self.type = type(type_name, (), {"name": type_name})()


def chat_member(status, *, type_name=None, can_post=True, can_delete=True):
    privileges = None
    if status == ChatMemberStatus.ADMINISTRATOR:
        privileges = ChatPrivileges(
            can_post_messages=can_post, can_delete_messages=can_delete)
    return ChatMember(
        status=status,
        chat=FakeChat(type_name) if type_name else None,
        privileges=privileges,
    )


class FakeClient:
    def __init__(self, *, me=None, member_result=None, messages_error=None,
                 me_error=None, member_error=None):
        self._me = me or User(id=1, first_name="Thunder", username="thunder_bot", is_bot=True)
        self._member_result = member_result
        self._messages_error = messages_error
        self._me_error = me_error
        self._member_error = member_error

    async def get_me(self):
        if self._me_error:
            raise self._me_error
        return self._me

    async def get_chat_member(self, chat_id, user_id):
        if self._member_error:
            raise self._member_error
        if isinstance(self._member_result, Exception):
            raise self._member_result
        return self._member_result

    async def get_messages(self, chat_id, message_ids=None):
        # Mirrors the real signature (ids, not limit): an earlier limit= call
        # raised TypeError that was misreported as a channel-permission failure.
        if message_ids is None:
            raise TypeError("message_ids is required")
        if self._messages_error:
            raise self._messages_error
        return None

    async def get_users(self, user_id):
        return self._me

    async def get_chat(self, chat_id):
        return object()


async def test_token_ok_names_the_bot():
    results = await preflight.check_bot_token(FakeClient())
    assert results[0][0] == preflight.OK
    assert "@thunder_bot" in results[0][1]


async def test_rejected_token_fails_and_names_the_variable():
    from pyrogram.errors import AccessTokenInvalid

    results = await preflight.check_bot_token(FakeClient(me_error=AccessTokenInvalid()))
    status, text = results[0]
    assert status == preflight.FAIL
    assert "BOT_TOKEN" in text
    assert "BotFather" in text, "tell the operator where to get a new token"


def test_token_rejected_message_names_the_env_line():
    text = preflight.token_rejected_message("MULTI_TOKEN1")
    assert "MULTI_TOKEN1" in text
    assert "rejected" in text
    assert "BotFather" in text, "tell the operator where to get a new token"


async def test_a_throttled_start_is_a_warning_not_a_failure():
    """A FloodWait must not stop a bot that worked yesterday."""
    results = await preflight.check_bot_token(FakeClient(me_error=FloodWait(value=37)))
    status, text = results[0]
    assert status == preflight.WARN
    assert "37" in text, "the wait should be quoted so the operator can judge it"


async def test_a_connection_error_only_warns():
    results = await preflight.check_bot_token(FakeClient(me_error=OSError("unreachable")))
    assert results[0][0] == preflight.WARN


async def test_admin_that_can_post_passes():
    client = FakeClient(member_result=chat_member(ChatMemberStatus.ADMINISTRATOR))
    results = await preflight.check_storage_channel(client, CHANNEL)
    assert [r[0] for r in results] == [preflight.OK]


async def test_admin_without_post_permission_fails():
    client = FakeClient(member_result=chat_member(ChatMemberStatus.ADMINISTRATOR, can_post=False))
    results = await preflight.check_storage_channel(client, CHANNEL)
    status, text = results[0]
    assert status == preflight.FAIL
    assert "Post messages" in text, "name the Telegram switch, not just the failure"


async def test_plain_member_fails_and_says_why():
    client = FakeClient(member_result=chat_member(ChatMemberStatus.MEMBER))
    results = await preflight.check_storage_channel(client, CHANNEL)
    status, text = results[0]
    assert status == preflight.FAIL
    assert "not an administrator" in text


async def test_not_a_member_is_distinct_from_lacking_rights():
    """Two different fixes, so two different messages."""
    client = FakeClient(member_error=UserNotParticipant())
    results = await preflight.check_storage_channel(client, CHANNEL)
    status, text = results[0]
    assert status == preflight.FAIL
    assert "not a member" in text
    assert "Make it an admin" not in text


async def test_owner_status_is_accepted():
    client = FakeClient(member_result=chat_member(ChatMemberStatus.OWNER))
    results = await preflight.check_storage_channel(client, CHANNEL)
    assert results[0][0] == preflight.OK


async def test_a_bot_cannot_use_get_history_so_the_probe_reads_an_id():
    """Bots get BOT_METHOD_INVALID from GetHistory, so the probe reads an id."""
    from pyrogram.errors import BadRequest

    class BotLike(FakeClient):
        async def get_chat_history(self, *args, **kwargs):
            raise BadRequest("Telegram says: [400 BOT_METHOD_INVALID]")

    client = BotLike(member_result=chat_member(ChatMemberStatus.ADMINISTRATOR))
    results = await preflight.check_storage_channel(client, CHANNEL, sample_message_id=123015)
    assert not [r for r in results if r[0] == preflight.FAIL]


async def test_unreadable_history_fails():
    client = FakeClient(member_result=chat_member(ChatMemberStatus.ADMINISTRATOR),
                        messages_error=ChatAdminRequired())
    results = await preflight.check_storage_channel(client, CHANNEL)
    assert results[0][0] == preflight.FAIL
    assert "404" in results[0][1], "say what the user would see"


async def test_a_missing_channel_is_named_as_such():
    client = FakeClient(member_error=PeerIdInvalid())
    results = await preflight.check_storage_channel(client, CHANNEL)
    assert results[0][0] == preflight.FAIL
    assert "no such channel" in results[0][1]


async def test_a_supergroup_storage_channel_is_not_failed_for_can_post_messages():
    """can_post_messages does not exist on a supergroup; failing on it blocks a working bot with impossible advice."""
    supergroup_admin = chat_member(
        ChatMemberStatus.ADMINISTRATOR, type_name="SUPERGROUP", can_post=False)
    client = FakeClient(member_result=supergroup_admin)
    results = await preflight.check_storage_channel(client, CHANNEL)
    assert not [r for r in results if r[0] == preflight.FAIL], (
        "a supergroup admin must not be told to enable a non-existent setting"
    )
    assert not any("Post messages" in text for _s, text in results), (
        "do not advise a switch that does not exist for supergroups"
    )


async def test_a_broadcast_channel_without_post_right_still_fails():
    channel_admin = chat_member(
        ChatMemberStatus.ADMINISTRATOR, type_name="CHANNEL", can_post=False)
    client = FakeClient(member_result=channel_admin)
    results = await preflight.check_storage_channel(client, CHANNEL)
    assert results[0][0] == preflight.FAIL


@pytest.mark.parametrize("type_name", ["CHANNEL", "MONOFORUM", "COMMUNITY"])
def test_broadcast_types_are_matched_by_name_not_enum_member(type_name):
    """Forum type is MONOFORUM or COMMUNITY across builds, so matching must be by name."""
    admin = chat_member(
        ChatMemberStatus.ADMINISTRATOR, type_name=type_name, can_post=False)
    assert preflight._is_broadcast_channel(admin) is True


def test_a_supergroup_is_not_a_broadcast_channel():
    admin = chat_member(ChatMemberStatus.ADMINISTRATOR, type_name="SUPERGROUP")
    assert preflight._is_broadcast_channel(admin) is False


async def test_missing_owner_id_warns_with_where_to_get_it():
    results = await preflight.check_owner(FakeClient(), 0)
    status, text = results[0]
    assert status == preflight.WARN
    assert "userinfobot" in text


async def test_an_unresolvable_owner_does_not_stop_startup():
    """A fresh bot has seen no users; owner checks use the numeric id, so an
    unresolved owner must not fail startup.
    """
    class Unresolvable(FakeClient):
        async def get_users(self, user_id):
            raise PeerIdInvalid()

    results = await preflight.check_owner(Unresolvable(), 123456)
    status, text = results[0]
    assert status == preflight.WARN, "a valid owner id must not fail startup"
    assert "message the bot once" in text


async def test_secondary_client_not_in_channel_fails_and_names_the_key():
    client = FakeClient(member_error=UserNotParticipant())
    results = await preflight.check_secondary_client(client, "MULTI_TOKEN1", CHANNEL)
    status, text = results[0]
    assert status == preflight.FAIL
    assert "MULTI_TOKEN1" in text
    assert "config.env" in text, "always offer the delete-the-line exit"


async def test_secondary_client_holds_the_same_bar_as_the_primary():
    client = FakeClient(member_result=chat_member(ChatMemberStatus.MEMBER))
    results = await preflight.check_secondary_client(client, "MULTI_TOKEN2", CHANNEL)
    assert results[0][0] == preflight.FAIL


async def test_a_token_from_the_wrong_bot_is_caught_by_its_id():
    """A valid token for a different bot passes every other check."""
    client = FakeClient(member_result=chat_member(ChatMemberStatus.ADMINISTRATOR))
    results = await preflight.check_secondary_client(
        client, "MULTI_TOKEN3", CHANNEL, expected_bot_id=999)
    status, text = results[0]
    assert status == preflight.FAIL
    assert "MULTI_TOKEN3" in text
    assert "wrong bot" in text


async def test_a_matching_token_id_passes():
    client = FakeClient(member_result=chat_member(ChatMemberStatus.ADMINISTRATOR))
    results = await preflight.check_secondary_client(
        client, "MULTI_TOKEN1", CHANNEL, expected_bot_id=1)  # FakeClient's bot id
    assert results[0][0] == preflight.OK


def test_token_id_is_read_from_the_prefix():
    assert preflight.token_bot_id("123456789:AAH-secret") == 123456789
    assert preflight.token_bot_id("not-a-token") is None
    assert preflight.token_bot_id("") is None


class FakeDatabase:
    def __init__(self, *, ping_error=None, write_error=None):
        self._ping_error = ping_error
        self._write_error = write_error

    async def ping(self):
        if self._ping_error:
            raise self._ping_error
        return {"ok": 1}

    async def verify_writable(self):
        if self._write_error:
            raise self._write_error
        return None


async def test_database_ok():
    results = await preflight.check_database(FakeDatabase())
    assert results[0][0] == preflight.OK


async def test_unreachable_database_warns_because_it_may_be_a_blip():
    results = await preflight.check_database(FakeDatabase(ping_error=ServerSelectionTimeoutError()))
    assert results[0][0] == preflight.WARN


async def test_rejected_login_fails_with_the_escaping_hint():
    results = await preflight.check_database(FakeDatabase(ping_error=OperationFailure("auth", code=18)))
    status, text = results[0]
    assert status == preflight.FAIL
    assert "%40" in text, "special characters in a password must be escaped"


async def test_a_read_only_user_fails_even_though_ping_worked():
    """The reason the write probe exists at all."""
    results = await preflight.check_database(FakeDatabase(write_error=OperationFailure("denied", code=13)))
    status, text = results[0]
    assert status == preflight.FAIL
    assert "cannot write" in text


# -- report rendering -----------------------------------------------------------

def test_success_is_one_line():
    report = preflight.Report()
    for _ in range(14):
        report.add(preflight.OK, "checked")
    rendered = report.render()
    assert rendered.count("✓") == 1, "a wall of ticks hides the one failure"
    assert "14 checks passed" in rendered


def test_a_failure_block_never_exceeds_five_lines():
    report = preflight.Report()
    report.add(preflight.FAIL, "✖ BOT_TOKEN: Telegram rejected this token.\n"
                               "  line two\n  line three\n  line four\n  line five\n  line six")
    report.add(preflight.OK, "fine")
    rendered = report.render()
    assert rendered.count("✖") >= 1


def test_failures_are_not_hidden_by_passing_checks():
    report = preflight.Report()
    report.add(preflight.OK, "fine")
    report.add(preflight.FAIL, "✖ BROKEN: something")
    rendered = report.render()
    assert "BROKEN" in rendered
    assert "14 checks" not in rendered


def test_verbose_shows_the_passing_checks():
    report = preflight.Report()
    report.add(preflight.OK, "channel fine")
    report.add(preflight.FAIL, "✖ BROKEN: something")
    assert "channel fine" in report.render(verbose=True)


def test_a_secret_is_never_rendered():
    """Logs get pasted into support chats."""
    report = preflight.Report()
    report.add(preflight.FAIL, f"✖ something about {preflight._describe(OperationFailure('x'))}")
    rendered = report.render()
    assert "mongodb://" not in rendered


def test_a_password_in_a_driver_error_is_not_repeated():
    """Mongo's own text can quote the connection string."""
    leaky = OperationFailure("auth failed for mongodb://user:secret@host/db", code=8000)
    described = preflight._describe(leaky)
    assert "secret" not in described
    assert "mongodb://" not in described


def test_corrections_are_summarised_without_values():
    report = preflight.Report()
    report.add(preflight.OK, "fine")
    rendered = report.render(corrections=["BOT_TOKEN", "HAS_SSL"])
    assert "BOT_TOKEN, HAS_SSL" in rendered
    assert "sk-" not in rendered


# -- the public-URL probe -------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, *, payload=None, error=None, body_is_json=True):
        self.status = status
        self._payload = payload
        self._error = error
        self._json = body_is_json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        if self._error:
            raise self._error
        return self._payload


class FakeHTTPSession:
    def __init__(self, response):
        self._response = response
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        if isinstance(self._response, BaseException):
            raise self._response
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def install_http(monkeypatch, response):
    session = FakeHTTPSession(response)
    monkeypatch.setattr(preflight.aiohttp, "ClientSession", lambda *a, **k: session)
    return session


async def test_the_probe_asks_the_status_endpoint(monkeypatch):
    session = install_http(monkeypatch, FakeResponse(payload={"server": {}}))
    await preflight.check_public_url("https://bot.example.com")
    assert session.requested == ["https://bot.example.com/status"]


async def test_a_trailing_slash_does_not_double_up(monkeypatch):
    session = install_http(monkeypatch, FakeResponse(payload={"server": {}}))
    await preflight.check_public_url("https://bot.example.com/")
    assert session.requested == ["https://bot.example.com/status"]


async def test_a_healthy_url_passes(monkeypatch):
    install_http(monkeypatch, FakeResponse(payload={"server": {"status": "operational"}}))
    results = await preflight.check_public_url("https://bot.example.com")
    assert results[0][0] == preflight.OK


async def test_a_non_200_is_reported_with_its_status(monkeypatch):
    install_http(monkeypatch, FakeResponse(status=502))
    results = await preflight.check_public_url("https://bot.example.com")
    status, text = results[0]
    assert status == preflight.WARN
    assert "502" in text


async def test_an_unreachable_host_is_a_warning_not_a_failure(monkeypatch):
    """Some proxies refuse the server's own request; that must not stop boot."""
    install_http(monkeypatch, aiohttp_connect_error())
    results = await preflight.check_public_url("https://bot.example.com")
    status, text = results[0]
    assert status == preflight.WARN
    assert "could not be reached" in text


def aiohttp_connect_error():
    import aiohttp
    return aiohttp.ClientConnectorDNSError(
        connection_key=aiohttp.client_reqrep.ConnectionKey(
            host="bot.example.com", port=443, is_ssl=True, ssl=True,
            proxy=None, proxy_auth=None, proxy_headers_hash=None),
        os_error=OSError("Name or service not known"),
    )


async def test_a_non_json_reply_is_not_described_as_unreachable(monkeypatch):
    """A 200 that is not JSON is a proxy login page, not a connection failure."""
    import aiohttp
    install_http(monkeypatch, FakeResponse(
        error=aiohttp.ContentTypeError(
            request_info=None, history=(), headers=None)))
    results = await preflight.check_public_url("https://bot.example.com")
    status, text = results[0]
    assert status == preflight.WARN
    assert "could not be reached" not in text
    assert "non-JSON" in text


async def test_json_from_another_service_is_caught(monkeypatch):
    """200 + JSON from something else on that address must not read as healthy."""
    install_http(monkeypatch, FakeResponse(payload={"message": "welcome to nginx"}))
    results = await preflight.check_public_url("https://bot.example.com")
    status, text = results[0]
    assert status == preflight.WARN
    assert "another service is probably bound" in text.lower()


async def test_a_json_array_is_caught(monkeypatch):
    """Parses as JSON but is not an object, so the shape check must handle it."""
    install_http(monkeypatch, FakeResponse(payload=["not", "a", "dict"]))
    results = await preflight.check_public_url("https://bot.example.com")
    assert results[0][0] == preflight.WARN


# -- the shortener probe ---------------------------------------------------------

async def test_the_shortener_is_skipped_when_off(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the shortener must not be called when it is off")

    monkeypatch.setattr(preflight, "_timed", boom)
    assert await preflight.check_shortener(False, "", "") == []


async def test_shortening_on_without_credentials_warns(monkeypatch):
    real = await preflight.check_shortener(True, "", "key")
    assert real[0][0] == preflight.WARN
    missing_site = await preflight.check_shortener(True, "cutt.ly", "")
    assert missing_site[0][0] == preflight.WARN


async def test_a_working_provider_passes(monkeypatch):
    import Thunder.utils.shortener as shortener

    async def fake_shorten(url):
        return "https://cutt.ly/abc123"

    monkeypatch.setattr(shortener, "shorten", fake_shorten)
    results = await preflight.check_shortener(True, "cutt.ly", "key")
    assert results[0][0] == preflight.OK
    assert "cutt.ly" in results[0][1]


async def test_a_provider_returning_the_link_unchanged_warns(monkeypatch):
    """The module falls back to the original URL on any failure; that is the signal."""
    import Thunder.utils.shortener as shortener

    async def fake_shorten(url):
        return url

    monkeypatch.setattr(shortener, "shorten", fake_shorten)
    results = await preflight.check_shortener(True, "cutt.ly", "key")
    status, text = results[0]
    assert status == preflight.WARN
    assert "unchanged" in text


async def test_an_empty_result_warns(monkeypatch):
    import Thunder.utils.shortener as shortener

    async def fake_shorten(url):
        return ""

    monkeypatch.setattr(shortener, "shorten", fake_shorten)
    results = await preflight.check_shortener(True, "cutt.ly", "key")
    assert results[0][0] == preflight.WARN


async def test_a_provider_error_warns_rather_than_failing_startup(monkeypatch):
    import Thunder.utils.shortener as shortener

    async def fake_shorten(url):
        raise RuntimeError("provider down")

    monkeypatch.setattr(shortener, "shorten", fake_shorten)
    results = await preflight.check_shortener(True, "cutt.ly", "key")
    assert results[0][0] == preflight.WARN
