"""Live checks for what only Telegram and MongoDB can answer.

Parsing proves a value is well formed; these prove it works. Each returns
``(status, text)``: ``ok``, ``warn`` or ``fail`` (startup stops unless transient).
"""

import asyncio
from typing import List, Optional, Sequence, Tuple

import aiohttp
from pyrogram.errors import (
    BadRequest,
    ChatAdminRequired,
    ChatForbidden,
    Flood,
    FloodWait,
    Forbidden,
    MessageIdInvalid,
    PeerIdInvalid,
    RpcCallFail,
    Unauthorized,
    UserNotParticipant,
)
from pyrogram.enums import ChatMemberStatus
from pymongo.errors import (
    AutoReconnect,
    ConfigurationError,
    ConnectionFailure,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from Thunder.utils.config_values import HEADER, FOOTER
from Thunder.utils.logger import logger


OK = "ok"
WARN = "warn"
FAIL = "fail"

CHECK_TIMEOUT_SECONDS = 8

Result = Tuple[str, str]


class Report:
    """Ordered results plus the rendering the operator sees."""

    def __init__(self):
        self.results: List[Result] = []

    def add(self, status: str, text: str) -> None:
        self.results.append((status, text))

    def extend(self, results: Sequence[Result]) -> None:
        self.results.extend(results)

    @property
    def failures(self) -> List[Result]:
        return [r for r in self.results if r[0] == FAIL]

    @property
    def warnings(self) -> List[Result]:
        return [r for r in self.results if r[0] == WARN]

    @property
    def passed(self) -> List[Result]:
        return [r for r in self.results if r[0] == OK]

    def render(self, *, verbose: bool = False, corrections: Sequence[str] = ()) -> str:
        """Problems only, unless ``verbose``: a wall of ticks hides the failure."""
        lines = [HEADER]

        if corrections:
            lines.append(f"   ◎ Corrections: {', '.join(sorted(set(corrections)))}.")

        problems = self.warnings + self.failures
        for _status, text in problems:
            lines.append("")
            for line in text.splitlines():
                lines.append(f"   {line}")

        if verbose:
            for status, text in self.passed:
                lines.append(f"   ✓ {text}")

        lines.append("")
        if self.failures:
            if len(self.failures) > 1:
                lines.append("   ✖ The bot did not start. Fix the failures above, then start it again.")
            else:
                lines.append("   ✖ The bot did not start. Fix the failure above, then start it again.")
        elif problems:
            lines.append("   ▶ Started with warnings. No action needed now.")
        else:
            # One line; a wall of ticks hides the failure.
            lines.append(f"   ✓ Configuration OK, {len(self.results)} checks passed.")

        # Last, so a tail-follow log still shows it.
        parts = [f"{len(self.passed)} passed"]
        if self.warnings:
            parts.append(f"{len(self.warnings)} warning{'s' if len(self.warnings) != 1 else ''}")
        if self.failures:
            parts.append(f"{len(self.failures)} failed")
        lines.append(f"   ▶ {len(self.results)} checks: {', '.join(parts)}.")

        lines.append(FOOTER)
        return "\n".join(lines)


def _describe(exc: BaseException) -> str:
    """A short, operator-readable reason. Never leaks a secret."""
    if isinstance(exc, FloodWait):
        return f"Telegram asked this account to wait {int(exc.value)} seconds"
    if isinstance(exc, ServerSelectionTimeoutError):
        return "the database did not answer in time"
    if isinstance(exc, ConfigurationError):
        return f"the connection string is malformed ({type(exc).__name__})"
    if isinstance(exc, OperationFailure):
        code = getattr(exc, "code", None)
        if code == 18:
            return "the database rejected the login"
        if code == 13:
            return "the login cannot write to this database"
        # The server's text can quote the connection string, password included.
        return f"the database returned an error (code {code})" if code else "the database returned an error"
    if isinstance(exc, ConnectionFailure):
        return "the database host could not be reached"
    return type(exc).__name__


def is_transient(exc: BaseException) -> bool:
    """A blip must not stop a bot that worked yesterday."""
    # asyncio.TimeoutError is TimeoutError, a subclass of OSError.
    return isinstance(exc, (Flood, AutoReconnect, ConnectionFailure,
                            aiohttp.ClientError, OSError)) or isinstance(exc, ServerSelectionTimeoutError)


async def _timed(coro, seconds: int = CHECK_TIMEOUT_SECONDS):
    return await asyncio.wait_for(coro, timeout=seconds)


# -- primary client -------------------------------------------------------------

def token_rejected_message(key: str) -> str:
    """Operator-readable text for a token Telegram rejected."""
    return (f"✖ {key}: Telegram rejected this token.\n"
            "  Open @BotFather, send /mybots, pick the bot, then\n"
            f"  \"API Token\". Copy the full token into {key}.")


async def check_bot_token(client) -> List[Result]:
    try:
        me = await _timed(client.get_me())
    except FloodWait as e:
        return [(WARN, f"⚠ BOT_TOKEN: {_describe(e)}.\n  Continuing after the wait. "
                       f"If this happens every start, wait longer between restarts.")]
    except Unauthorized:
        return [(FAIL, token_rejected_message("BOT_TOKEN"))]
    except (BadRequest, RpcCallFail) as e:
        text = str(e)
        if "API_ID" in text or "api_id" in text.lower():
            return [(FAIL, "✖ API_ID or API_HASH was rejected by Telegram.\n"
                           "  Open my.telegram.org/apps and check both values.\n"
                           "  They must come from the same app, with no spaces.")]
        return [(FAIL, "✖ BOT_TOKEN: Telegram rejected this token.\n"
                       "  Open @BotFather, send /mybots, pick the bot, then\n"
                       "  \"API Token\". Copy the full token into BOT_TOKEN.")]
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ BOT_TOKEN: could not check it ({_describe(e)}).")]
        return [(FAIL, f"✖ BOT_TOKEN: could not be verified ({_describe(e)}).")]

    name = f"@{me.username}" if getattr(me, "username", None) else me.first_name
    return [(OK, f"BOT_TOKEN verified with Telegram as {name}")]


async def check_storage_channel(client, channel_id: int, sample_message_id: Optional[int] = None) -> List[Result]:
    """Membership, posting right and read access, in dependency order."""
    results: List[Result] = []
    try:
        member = await _timed(client.get_chat_member(channel_id, "me"))
    except UserNotParticipant:
        return [(FAIL, f"✖ BIN_CHANNEL {channel_id}: the bot is not a member.\n"
                       "  Open the channel, then Administrators, then add the bot\n"
                       "  with \"Post messages\" on.\n"
                       "  If this is not your storage channel, correct BIN_CHANNEL.")]
    except ChatAdminRequired:
        return [(FAIL, f"✖ BIN_CHANNEL {channel_id}: the bot cannot see the member list.\n"
                       "  Add it as an administrator of that channel.")]
    except (ChatForbidden, Forbidden):
        return [(FAIL, f"✖ BIN_CHANNEL {channel_id}: the bot cannot access it.\n"
                       "  Check the id, and add the bot to that channel.")]
    except (PeerIdInvalid, KeyError):
        return [(FAIL, f"✖ BIN_CHANNEL {channel_id}: no such channel.\n"
                       "  Copy the id from the channel's message link, including\n"
                       "  the -100 prefix.")]
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ BIN_CHANNEL {channel_id}: could not be checked ({_describe(e)}).")]
        return [(FAIL, f"✖ BIN_CHANNEL {channel_id}: could not be checked ({_describe(e)}).")]

    status = getattr(member, "status", None)
    if status == ChatMemberStatus.BANNED:
        return [(FAIL, f"✖ BIN_CHANNEL {channel_id}: the bot is banned there.\n"
                       "  Remove the ban, or use a different channel.")]
    if status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return [(FAIL, f"✖ BIN_CHANNEL {channel_id}: the bot cannot post.\n"
                       "  It is in the channel, but it is not an administrator.\n"
                       "  Make it an admin with \"Post messages\" on.")]

    privileges = getattr(member, "privileges", None)
    # Only broadcast channels set this; a supergroup has no such switch, so
    # failing on it would block a working deployment.
    if _is_broadcast_channel(member) and getattr(privileges, "can_post_messages", None) is False:
        return [(FAIL, f"✖ BIN_CHANNEL {channel_id}: the bot cannot post.\n"
                       "  It is an administrator, but \"Post messages\" is off.\n"
                       "  Open the channel, then Administrators, then the bot,\n"
                       "  turn on \"Post messages\", then save.")]

    try:
        await _timed(_can_read_history(client, channel_id, sample_message_id))
    except MessageIdInvalid:
        # A stale sample id is not a read problem: membership was verified above.
        pass
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ BIN_CHANNEL {channel_id}: read access could not be checked ({_describe(e)}).")]
        if isinstance(e, (TypeError, AttributeError)):
            # Our call was wrong, not the operator's setup.
            logger.error(f"Preflight read check misused the client: {e}", exc_info=True)
            return [(WARN, f"⚠ BIN_CHANNEL {channel_id}: read access could not be checked "
                           "(the check itself failed; see the log).")]
        return [(FAIL, f"✖ BIN_CHANNEL {channel_id}: the bot cannot read messages there,\n"
                       "  so every link would return 404. Make it an administrator\n"
                       "  with \"Post messages\" on.")]

    can_delete = getattr(privileges, "can_delete_messages", None)
    if can_delete is False:
        results.append((WARN, f"⚠ BIN_CHANNEL {channel_id}: \"Delete messages\" is off,\n"
                              "  so replaced files cannot be cleaned up."))
    else:
        role = "owner" if status == ChatMemberStatus.OWNER else "administrator"
        results.append((OK, f"BIN_CHANNEL {channel_id}: {role}, can post, can read"))
    return results


async def check_owner(client, owner_id: int) -> List[Result]:
    if not owner_id:
        return [(WARN, "⚠ OWNER_ID is not set. Nobody can use owner commands,\n"
                       "  including /status and /broadcast.\n"
                       "  Send /start to @userinfobot, then set OWNER_ID.")]
    try:
        await _timed(client.get_users(owner_id))
    except (PeerIdInvalid, IndexError):
        # Normal on a fresh install; owner checks match the numeric id anyway.
        return [(WARN, f"⚠ OWNER_ID {owner_id}: Telegram cannot resolve this user yet.\n"
                       "  That is normal until they message the bot once.\n"
                       "  If the id is wrong, check it with @userinfobot.")]
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ OWNER_ID {owner_id}: could not be checked ({_describe(e)}).")]
        return [(WARN, f"⚠ OWNER_ID {owner_id}: could not be verified ({_describe(e)}).")]
    return [(OK, f"OWNER_ID {owner_id} verified")]


async def check_force_channel(client, channel_id: int) -> List[Result]:
    try:
        await _timed(client.get_chat(channel_id))
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ FORCE_CHANNEL_ID {channel_id}: could not be checked ({_describe(e)}).")]
        return [(WARN, f"⚠ FORCE_CHANNEL_ID {channel_id}: not accessible.\n"
                       "  Users would be asked to join a channel the bot cannot read.")]

    try:
        await _timed(client.get_chat_member(channel_id, "me"))
    except UserNotParticipant:
        return [(WARN, f"⚠ FORCE_CHANNEL_ID {channel_id}: the bot is not a member.\n"
                       "  Users would be asked to join a channel it cannot check.\n"
                       "  Add the bot to that channel.")]
    except Exception:
        return [(OK, f"FORCE_CHANNEL_ID {channel_id} readable")]
    return [(OK, f"FORCE_CHANNEL_ID {channel_id} readable, bot is a member")]


async def storage_channel_problem(client, channel_id: int,
                                 sample_message_id: Optional[int] = None) -> Optional[str]:
    """The storage-channel problem, or None if fine.

    Shares check_storage_channel's rules so the watcher cannot disagree with
    startup. Transient errors return None: a blip is not a permission change.
    """
    results = await check_storage_channel(client, channel_id, sample_message_id)
    if any(status == FAIL for status, _text in results):
        return results[0][1]
    return None


def add_host_drift_warnings(report) -> None:
    """Missing-host / ignored-port warnings startup adds after boot.

    Shared so /doctor shows the same drift.
    """
    from Thunder.utils.public_host import describe_ignored_port, describe_missing_host
    from Thunder.vars import Var

    if not Var.FORCE_CHANNEL_ID and Var.FORCE_CHANNEL_PROBLEM:
        report.add("warn", f"⚠ {Var.FORCE_CHANNEL_PROBLEM}")
    missing_host = describe_missing_host(Var.FQDN)
    if missing_host:
        report.add("warn", f"⚠ {missing_host}.")
    port_note = describe_ignored_port(Var.FQDN_PORT, Var.PORT, Var.NO_PORT)
    if port_note:
        report.add("warn", f"⚠ {port_note}")


async def verify_configuration(report) -> None:
    """Checks only Telegram and Mongo can answer, reported as one block.

    Shared by startup and /doctor: independent checks run concurrently; the
    channel check runs after them because it reads a stored message id.
    """
    from Thunder.bot import StreamBot
    from Thunder.utils.database import db
    from Thunder.vars import Var

    sample_task = asyncio.create_task(sample_message_id(db))
    independent = [
        check_bot_token(StreamBot),
        check_owner(StreamBot, Var.OWNER_ID),
        check_database(db),
        check_shortener(
            Var.SHORTEN_ENABLED or Var.SHORTEN_MEDIA_LINKS,
            Var.URL_SHORTENER_SITE,
            Var.URL_SHORTENER_API_KEY,
        ),
    ]
    if Var.FORCE_CHANNEL_ID:
        independent.append(check_force_channel(StreamBot, Var.FORCE_CHANNEL_ID))

    results = await asyncio.gather(*independent, return_exceptions=True)
    for outcome in results:
        # Each check catches its own errors, so this only fires on a bug in one.
        if isinstance(outcome, BaseException):
            logger.error("A configuration check raised unexpectedly", exc_info=outcome)
            report.add("warn", "⚠ A configuration check could not be completed.\n"
                               "  See the log for the traceback.")
            continue
        report.extend(outcome)

    report.extend(await check_storage_channel(StreamBot, Var.BIN_CHANNEL, await sample_task))
    add_host_drift_warnings(report)


def _is_broadcast_channel(member) -> bool:
    """Whether ``can_post_messages`` is meaningful for this chat.

    A supergroup leaves it False because admins post through membership. Match
    by name: the forum type is MONOFORUM in one library build, COMMUNITY in
    another. Absent type: assume a channel.
    """
    chat = getattr(member, "chat", None)
    chat_type = getattr(chat, "type", None)
    if chat_type is None:
        return True
    return getattr(chat_type, "name", "") in ("CHANNEL", "MONOFORUM", "COMMUNITY")


def token_bot_id(token: str) -> Optional[int]:
    """The bot id encoded before the colon in a token.

    Telegram rejects a prefix that disagrees with the named bot only after a
    connection, so this lets the mismatch be reported by name.
    """
    prefix, separator, _secret = (token or "").partition(":")
    if not separator or not prefix.strip().isdigit():
        return None
    return int(prefix)


async def sample_message_id(database) -> Optional[int]:
    """A recorded stored message, if any.

    A real file proves the exact read streaming performs. Lookup failure is
    ignored; the check falls back to message 1.
    """
    try:
        record = await asyncio.wait_for(
            database.files_col.find_one({}, sort=[("created_at", -1)]), timeout=5)
    except Exception:
        return None
    if not record:
        return None
    value = record.get("canonical_message_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _can_read_history(client, channel_id: int, message_id: Optional[int] = None) -> None:
    """Read one message; bots cannot use GetHistory.

    ``get_chat_history`` fails with BOT_METHOD_INVALID. Without a known id, try
    message 1; an unreachable channel fails differently from an absent message.
    """
    await client.get_messages(channel_id, message_id or 1)


async def check_secondary_client(client, key: str, channel_id: int,
                                 expected_bot_id: Optional[int] = None) -> List[Result]:
    """An extra token is held to the primary's bar, since it serves too.

    ``expected_bot_id`` comes from the token; comparing it with ``get_me`` catches
    a token copied from the wrong bot, which otherwise passes every check.
    """
    try:
        me = await _timed(client.get_me())
    except FloodWait as e:
        return [(WARN, f"⚠ {key}: {_describe(e)}.\n"
                       "  Continuing after the wait. If this happens on\n"
                       f"  every start, remove {key} from config.env.")]
    except Exception as e:
        return [(WARN, f"⚠ {key}: could not be started ({_describe(e)}).\n"
                       f"  Remove {key} from config.env to stop trying it.")]

    if expected_bot_id is not None and getattr(me, "id", None) != expected_bot_id:
        return [(FAIL, f"✖ {key}: the token belongs to bot id {expected_bot_id}, but\n"
                       f"  Telegram authenticated it as {getattr(me, 'id', '?')}.\n"
                       f"  The token was probably copied from the wrong bot.\n"
                       f"  Paste the token from @BotFather for the bot you meant.")]

    name = f"@{me.username}" if getattr(me, "username", None) else f"id {me.id}"

    try:
        member = await _timed(client.get_chat_member(channel_id, "me"))
    except UserNotParticipant:
        return [(FAIL, f"✖ {key} {name}: not in BIN_CHANNEL {channel_id}.\n"
                       "  Streaming is shared, so some requests would fail until it\n"
                       "  can read the channel. Add the bot as an administrator, or\n"
                       f"  delete the {key} line from config.env.")]
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ {key} {name}: channel access could not be checked ({_describe(e)}).")]
        return [(FAIL, f"✖ {key} {name}: cannot access BIN_CHANNEL {channel_id}.\n"
                       f"  Add it as an administrator there, or delete the {key} line.")]

    privileges = getattr(member, "privileges", None)
    if getattr(member, "status", None) not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return [(FAIL, f"✖ {key} {name}: not an administrator in BIN_CHANNEL.\n"
                       f"  Add that bot as an administrator there, or delete the\n"
                       f"  {key} line from config.env.")]
    if _is_broadcast_channel(member) and getattr(privileges, "can_post_messages", None) is False:
        return [(FAIL, f"✖ {key} {name}: \"Post messages\" is off in BIN_CHANNEL.\n"
                       "  Turn it on for that bot, or delete the line from config.env.")]

    try:
        await _timed(_can_read_history(client, channel_id))
    except Exception as e:
        if isinstance(e, (TypeError, AttributeError)):
            logger.error(f"Preflight read check misused the client: {e}", exc_info=True)
            return [(WARN, f"⚠ {key} {name}: history could not be checked "
                           "(the check itself failed; see the log).")]
        if not is_transient(e):
            return [(FAIL, f"✖ {key} {name}: cannot read BIN_CHANNEL.\n"
                           "  Links served by this bot would return 404.")]
        return [(WARN, f"⚠ {key} {name}: history read could not be checked ({_describe(e)}).")]
    return [(OK, f"{key} {name}: administrator, can post, can read")]


# -- database -------------------------------------------------------------------

async def check_database(database) -> List[Result]:
    try:
        await _timed(database.ping())
    except ServerSelectionTimeoutError as e:
        return [(WARN, f"⚠ DATABASE_URL: {_describe(e)}.\n"
                       "  If the host is right, check the username and password:\n"
                       "  wrong credentials can also show as no answer.")]
    except OperationFailure as e:
        return [(FAIL, f"✖ DATABASE_URL: {_describe(e)}.\n"
                       "  Check the username and password in the connection string.\n"
                       "  If the password has @ : / or %, write each one as\n"
                       "  %40 %3A %2F %25.")]
    except ConfigurationError as e:
        return [(FAIL, f"✖ DATABASE_URL: {_describe(e)}.\n"
                       "  It must start with mongodb:// or mongodb+srv://.")]
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ DATABASE_URL: {_describe(e)}.")]
        return [(FAIL, f"✖ DATABASE_URL: {_describe(e)}.")]

    try:
        await _timed(database.verify_writable())
    except OperationFailure as e:
        code = getattr(e, "code", None)
        if code == 13:
            return [(FAIL, "✖ DATABASE_URL: connected, but the login cannot write.\n"
                           "  Give this user read and write access to the database.\n"
                           "  On Atlas, use the role \"Read and write to any database\".")]
        return [(FAIL, f"✖ DATABASE_URL: connected, but the write failed ({_describe(e)}).")]
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ DATABASE_URL: the write could not be checked ({_describe(e)}).")]
        return [(FAIL, f"✖ DATABASE_URL: the write failed ({_describe(e)}).")]
    return [(OK, "DATABASE_URL connected and writable")]


# -- optional features ----------------------------------------------------------

async def check_shortener(enabled: bool, site: str, key: str) -> List[Result]:
    if not enabled:
        return []
    if not (site and key):
        return [(WARN, "⚠ URL shortening is on, but URL_SHORTENER_SITE or\n"
                       "  URL_SHORTENER_API_KEY is empty, so links are not shortened.")]

    from Thunder.utils.shortener import shorten

    sample = "https://t.me/thunder_untested"
    try:
        result = await _timed(shorten(sample), seconds=15)
    except Exception as e:
        return [(WARN, f"⚠ URL_SHORTENER_SITE: the provider did not answer ({_describe(e)}).")]
    if not result or result == sample:
        return [(WARN, "⚠ URL_SHORTENER_SITE: the provider returned the link unchanged.\n"
                       "  Check URL_SHORTENER_API_KEY; links are not being shortened.")]
    return [(OK, f"URL shortener {site} working")]


async def check_public_url(url: str) -> List[Result]:
    """Ask the bot's own URL for /status.

    Three failures, three fixes: unreachable, reachable but not JSON, reachable
    but not this bot's reply.
    """
    target = url.rstrip("/") + "/status"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(target) as response:
                if response.status != 200:
                    return [(WARN, f"⚠ {url} answered /status with {response.status}.\n"
                                   "  Check FQDN, PORT and your reverse proxy.")]
                payload = await response.json(content_type=None)
    except aiohttp.ContentTypeError:
        return [(WARN, f"⚠ {url} answered /status with a non-JSON body.\n"
                       "  Something other than this bot is serving that address.\n"
                       "  Check FQDN and your reverse proxy.")]
    except Exception as e:
        return [(WARN, f"⚠ {url} could not be reached ({type(e).__name__}).\n"
                       "  Check FQDN, PORT and your reverse proxy. Some proxies\n"
                       "  refuse requests from the server to itself.")]

    if not isinstance(payload, dict) or "server" not in payload:
        return [(WARN, f"⚠ {url} answered /status, but not with this bot's reply.\n"
                       "  Another service is probably bound to that address.\n"
                       "  Check FQDN and your reverse proxy.")]
    return [(OK, f"Public URL {url} serving")]

