"""Live Telegram/Mongo checks returning (status, text)."""

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
    InternalServerError,
    MessageIdInvalid,
    PeerIdInvalid,
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
from Thunder.utils.messages import (
    MSG_PREFLIGHT_API_REJECTED,
    MSG_PREFLIGHT_CHANNEL_BANNED,
    MSG_PREFLIGHT_CHANNEL_CANNOT_POST,
    MSG_PREFLIGHT_CHANNEL_DELETE_DISABLED,
    MSG_PREFLIGHT_CHANNEL_INACCESSIBLE,
    MSG_PREFLIGHT_CHANNEL_NO_MEMBER_LIST,
    MSG_PREFLIGHT_CHANNEL_NOT_FOUND,
    MSG_PREFLIGHT_CHANNEL_NOT_MEMBER,
    MSG_PREFLIGHT_CHANNEL_POST_DISABLED,
    MSG_PREFLIGHT_CHANNEL_UNREADABLE,
    MSG_PREFLIGHT_CHECK_CRASHED,
    MSG_PREFLIGHT_DB_LOGIN_REJECTED,
    MSG_PREFLIGHT_DB_MALFORMED_URL,
    MSG_PREFLIGHT_DB_READ_ONLY,
    MSG_PREFLIGHT_DB_TIMEOUT,
    MSG_PREFLIGHT_FORCE_INACCESSIBLE,
    MSG_PREFLIGHT_FORCE_NOT_MEMBER,
    MSG_PREFLIGHT_OWNER_UNRESOLVED,
    MSG_PREFLIGHT_OWNER_UNSET,
    MSG_PREFLIGHT_SECONDARY_CHANNEL_DENIED,
    MSG_PREFLIGHT_SECONDARY_NOT_ADMIN,
    MSG_PREFLIGHT_SECONDARY_NOT_IN_CHANNEL,
    MSG_PREFLIGHT_SECONDARY_POST_DISABLED,
    MSG_PREFLIGHT_SECONDARY_START_FAILED,
    MSG_PREFLIGHT_SECONDARY_THROTTLED,
    MSG_PREFLIGHT_SECONDARY_UNREADABLE,
    MSG_PREFLIGHT_SECONDARY_WRONG_BOT,
    MSG_PREFLIGHT_SHORTENER_MISSING_CREDS,
    MSG_PREFLIGHT_SHORTENER_UNCHANGED,
    MSG_PREFLIGHT_TOKEN_REJECTED,
    MSG_PREFLIGHT_TOKEN_THROTTLED,
    MSG_PREFLIGHT_URL_BAD_STATUS,
    MSG_PREFLIGHT_URL_FOREIGN_REPLY,
    MSG_PREFLIGHT_URL_NON_JSON,
    MSG_PREFLIGHT_URL_UNREACHABLE,
)


OK = "ok"
WARN = "warn"
FAIL = "fail"

# Outlast pymongo's serverSelectionTimeoutMS (10000 ms) so its own rich
# ServerSelectionTimeoutError is raised instead of our bare wait_for timeout.
CHECK_TIMEOUT_SECONDS = 12

Result = Tuple[str, str]


class InconclusiveProbe(Exception):
    """The check neither confirmed access nor found a definite access problem."""


class Report:
    """Ordered results plus the rendering the operator sees."""

    def __init__(self):
        self.results: List[Result] = []
        # Cache status buckets for render().
        self._failures: List[Result] = []
        self._warnings: List[Result] = []
        self._passed: List[Result] = []

    def add(self, status: str, text: str) -> None:
        result = (status, text)
        self.results.append(result)
        self._tally(result)

    def extend(self, results: Sequence[Result]) -> None:
        self.results.extend(results)
        for result in results:
            self._tally(result)

    def _tally(self, result: Result) -> None:
        if result[0] == FAIL:
            self._failures.append(result)
        elif result[0] == WARN:
            self._warnings.append(result)
        elif result[0] == OK:
            self._passed.append(result)

    @property
    def failures(self) -> List[Result]:
        return list(self._failures)

    @property
    def warnings(self) -> List[Result]:
        return list(self._warnings)

    @property
    def passed(self) -> List[Result]:
        return list(self._passed)

    def render(self, *, verbose: bool = False, corrections: Sequence[str] = (),
               diagnostic: bool = False) -> str:
        """Problems only, unless ``verbose``: a wall of ticks hides the failure."""
        lines = [HEADER]

        if corrections:
            lines.append(f"   ◎ Corrections: {', '.join(sorted(set(corrections)))}.")

        problems = self.warnings + self.failures
        for _status, text in problems:
            problem_lines = text.splitlines()
            if len(problem_lines) > 5:
                problem_lines = problem_lines[:4] + ["…"]
            lines.append("")
            for line in problem_lines:
                lines.append(f"   {line}")

        if verbose:
            for status, text in self.passed:
                lines.append(f"   ✓ {text}")

        lines.append("")
        if self.failures:
            if diagnostic:
                lines.append("   ✖ Problems found above. Fix them, then restart to apply.")
            elif len(self.failures) > 1:
                lines.append("   ✖ The bot did not start. Fix the failures above, then start it again.")
            else:
                lines.append("   ✖ The bot did not start. Fix the failure above, then start it again.")
        elif problems:
            lines.append("   ▶ Started with warnings. No action needed now.")
        else:
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

def _describe(exc: BaseException, subject: str = "the database") -> str:
    """A short, operator-readable reason. Never leaks a secret."""
    if isinstance(exc, FloodWait):
        return f"Telegram asked this account to wait {int(exc.value)} seconds"
    if isinstance(exc, ServerSelectionTimeoutError):
        return "the database did not answer in time"
    if isinstance(exc, asyncio.TimeoutError):
        # wait_for can still win the race if pymongo's own timeout is raised
        # later than ours; describe it the same way, not as a class name.
        return f"{subject} did not answer in time"
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
    """Whether a failed live check should warn instead of stopping startup."""
    # asyncio.TimeoutError is TimeoutError, a subclass of OSError.
    return isinstance(exc, (Flood, AutoReconnect, ConnectionFailure, InternalServerError,
                            aiohttp.ClientError, OSError)) or isinstance(exc, ServerSelectionTimeoutError)


async def _timed(coro, seconds: int = CHECK_TIMEOUT_SECONDS):
    return await asyncio.wait_for(coro, timeout=seconds)


# -- primary client -------------------------------------------------------------

def token_rejected_message(key: str) -> str:
    return MSG_PREFLIGHT_TOKEN_REJECTED.format(key=key)


async def check_bot_token(client) -> List[Result]:
    try:
        me = await _timed(client.get_me())
    except FloodWait as e:
        return [(WARN, MSG_PREFLIGHT_TOKEN_THROTTLED.format(reason=_describe(e)))]
    except Unauthorized:
        return [(FAIL, token_rejected_message("BOT_TOKEN"))]
    except BadRequest as e:
        text = str(e)
        if "API_ID" in text or "api_id" in text.lower():
            return [(FAIL, MSG_PREFLIGHT_API_REJECTED)]
        return [(FAIL, token_rejected_message("BOT_TOKEN"))]
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ BOT_TOKEN: could not check it ({_describe(e, 'Telegram')}).")]
        return [(FAIL, f"✖ BOT_TOKEN: could not be verified ({_describe(e, 'Telegram')}).")]

    name = f"@{me.username}" if getattr(me, "username", None) else me.first_name
    return [(OK, f"BOT_TOKEN verified with Telegram as {name}")]


async def check_storage_channel(client, channel_id: int, sample_message_id: Optional[int] = None) -> List[Result]:
    """Membership, posting right and read access, in dependency order."""
    results: List[Result] = []
    try:
        member = await _timed(client.get_chat_member(channel_id, "me"))
    except UserNotParticipant:
        return [(FAIL, MSG_PREFLIGHT_CHANNEL_NOT_MEMBER.format(channel_id=channel_id))]
    except ChatAdminRequired:
        return [(FAIL, MSG_PREFLIGHT_CHANNEL_NO_MEMBER_LIST.format(channel_id=channel_id))]
    except (ChatForbidden, Forbidden):
        return [(FAIL, MSG_PREFLIGHT_CHANNEL_INACCESSIBLE.format(channel_id=channel_id))]
    except (PeerIdInvalid, KeyError):
        return [(FAIL, MSG_PREFLIGHT_CHANNEL_NOT_FOUND.format(channel_id=channel_id))]
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ BIN_CHANNEL {channel_id}: could not be checked ({_describe(e, 'Telegram')}).")]
        return [(FAIL, f"✖ BIN_CHANNEL {channel_id}: could not be checked ({_describe(e, 'Telegram')}).")]

    status = getattr(member, "status", None)
    if status == ChatMemberStatus.BANNED:
        return [(FAIL, MSG_PREFLIGHT_CHANNEL_BANNED.format(channel_id=channel_id))]
    if status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return [(FAIL, MSG_PREFLIGHT_CHANNEL_CANNOT_POST.format(channel_id=channel_id))]

    privileges = getattr(member, "privileges", None)
    # ChatMember._parse omits chat for admins/owners; resolve it before
    # applying channel-only rights.
    if getattr(privileges, "can_post_messages", None) is False:
        try:
            if await _timed(_is_broadcast_target(client, member, channel_id)):
                return [(FAIL, MSG_PREFLIGHT_CHANNEL_POST_DISABLED.format(channel_id=channel_id))]
        except Exception as e:
            return [(WARN, f"⚠ BIN_CHANNEL {channel_id}: posting right could not be checked "
                           f"({_describe(e, 'Telegram')}).")]

    try:
        await _timed(_can_read_history(client, channel_id, sample_message_id))
    except MessageIdInvalid:
        # A stale sample id is not a read problem: membership was verified above.
        pass
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ BIN_CHANNEL {channel_id}: read access could not be checked "
                           f"({_describe(e, 'Telegram')}).")]
        if isinstance(e, (TypeError, AttributeError)):
            # Our call was wrong, not the operator's setup.
            logger.error(f"Preflight read check misused the client: {e}", exc_info=True)
            return [(WARN, f"⚠ BIN_CHANNEL {channel_id}: read access could not be checked "
                           "(the check itself failed; see the log).")]
        return [(FAIL, MSG_PREFLIGHT_CHANNEL_UNREADABLE.format(channel_id=channel_id))]

    can_delete = getattr(privileges, "can_delete_messages", None)
    if can_delete is False:
        results.append((WARN, MSG_PREFLIGHT_CHANNEL_DELETE_DISABLED.format(channel_id=channel_id)))
    role = "owner" if status == ChatMemberStatus.OWNER else "administrator"
    results.append((OK, f"BIN_CHANNEL {channel_id}: {role}, can post, can read"))
    return results


async def check_owner(client, owner_id: int) -> List[Result]:
    if not owner_id:
        return [(WARN, MSG_PREFLIGHT_OWNER_UNSET)]
    try:
        await _timed(client.get_users(owner_id))
    except (PeerIdInvalid, IndexError):
        # Normal on a fresh install; owner checks match the numeric id anyway.
        return [(WARN, MSG_PREFLIGHT_OWNER_UNRESOLVED.format(owner_id=owner_id))]
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ OWNER_ID {owner_id}: could not be checked ({_describe(e, 'Telegram')}).")]
        return [(WARN, f"⚠ OWNER_ID {owner_id}: could not be verified ({_describe(e, 'Telegram')}).")]
    return [(OK, f"OWNER_ID {owner_id} verified")]


async def check_force_channel(client, channel_id: int) -> List[Result]:
    try:
        await _timed(client.get_chat(channel_id))
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ FORCE_CHANNEL_ID {channel_id}: could not be checked ({_describe(e, 'Telegram')}).")]
        return [(WARN, MSG_PREFLIGHT_FORCE_INACCESSIBLE.format(channel_id=channel_id))]

    try:
        await _timed(client.get_chat_member(channel_id, "me"))
    except UserNotParticipant:
        return [(WARN, MSG_PREFLIGHT_FORCE_NOT_MEMBER.format(channel_id=channel_id))]
    except Exception as e:
        # Optional feature: probe failures only warn.
        return [(WARN, f"⚠ FORCE_CHANNEL_ID {channel_id}: could not check membership "
                       f"({_describe(e, 'Telegram')}).")]
    return [(OK, f"FORCE_CHANNEL_ID {channel_id} readable, bot is a member")]


async def storage_channel_problem(client, channel_id: int,
                                 sample_message_id: Optional[int] = None) -> Optional[str]:
    """Return a definite result; raise when the probe is inconclusive."""
    results = await check_storage_channel(client, channel_id, sample_message_id)
    if any(status == FAIL for status, _text in results):
        return next(text for status, text in results if status == FAIL)
    if any(status == OK for status, _text in results):
        return None
    raise InconclusiveProbe


async def probe_storage_channel(client, database, channel_id: int) -> Optional[str]:
    """Return a definite watcher result; raise when access is inconclusive."""
    message_id = await sample_message_id(database)
    return await storage_channel_problem(client, channel_id, message_id)


def add_host_drift_warnings(report) -> None:
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
    """Run checks concurrently; only the channel check waits for a sample ID."""
    from Thunder.bot import StreamBot
    from Thunder.utils.database import db
    from Thunder.vars import Var

    sample_task = asyncio.create_task(sample_message_id(db))
    try:
        async def check_channel():
            return await check_storage_channel(StreamBot, Var.BIN_CHANNEL, await sample_task)

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
        independent.append(check_channel())

        results = await asyncio.gather(*independent, return_exceptions=True)
        for outcome in results:
            # Each check catches its own errors, so this only fires on a bug in one.
            if isinstance(outcome, BaseException):
                logger.error("A configuration check raised unexpectedly", exc_info=outcome)
                report.add(WARN, MSG_PREFLIGHT_CHECK_CRASHED)
                continue
            report.extend(outcome)

        add_host_drift_warnings(report)
    finally:
        if not sample_task.done():
            sample_task.cancel()
        try:
            await sample_task
        except asyncio.CancelledError:
            pass


def _is_broadcast_chat(chat) -> bool:
    """Match forum type names across builds; unknown types default to channel."""
    chat_type = getattr(chat, "type", None)
    if chat_type is None:
        return True
    return getattr(chat_type, "name", "") in ("CHANNEL", "MONOFORUM", "COMMUNITY")


def _is_broadcast_channel(member) -> bool:
    return _is_broadcast_chat(getattr(member, "chat", None))


async def _is_broadcast_target(client, member, channel_id: int) -> bool:
    """Resolve the chat type when ChatMember does not include its chat."""
    chat = getattr(member, "chat", None)
    if getattr(chat, "type", None) is None:
        chat = await client.get_chat(channel_id)
    return _is_broadcast_chat(chat)


def token_bot_id(token: str) -> Optional[int]:
    """Read the bot ID prefix to detect mismatched tokens before channel checks."""
    prefix, separator, _secret = (token or "").partition(":")
    if not separator or not prefix.strip().isdigit():
        return None
    return int(prefix)


async def sample_message_id(database) -> Optional[int]:
    """Return a stored message ID for the read probe, or None."""
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
    """Read one message; bots cannot use GetHistory."""
    await client.get_messages(channel_id, message_id or 1)


async def check_secondary_client(client, key: str, channel_id: int,
                                 expected_bot_id: Optional[int] = None) -> List[Result]:
    """Check a serving secondary client, including its token-prefix bot ID."""
    try:
        me = await _timed(client.get_me())
    except FloodWait as e:
        return [(WARN, MSG_PREFLIGHT_SECONDARY_THROTTLED.format(key=key, reason=_describe(e)))]
    except Unauthorized:
        return [(FAIL, token_rejected_message(key))]
    except Exception as e:
        return [(WARN, MSG_PREFLIGHT_SECONDARY_START_FAILED.format(
            key=key, reason=_describe(e, 'Telegram')))]

    if expected_bot_id is not None and getattr(me, "id", None) != expected_bot_id:
        return [(FAIL, MSG_PREFLIGHT_SECONDARY_WRONG_BOT.format(
            key=key, expected_bot_id=expected_bot_id, actual_id=getattr(me, "id", "?")))]

    name = f"@{me.username}" if getattr(me, "username", None) else f"id {me.id}"

    try:
        member = await _timed(client.get_chat_member(channel_id, "me"))
    except UserNotParticipant:
        return [(FAIL, MSG_PREFLIGHT_SECONDARY_NOT_IN_CHANNEL.format(
            key=key, name=name, channel_id=channel_id))]
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ {key} {name}: channel access could not be checked "
                           f"({_describe(e, 'Telegram')}).")]
        return [(FAIL, MSG_PREFLIGHT_SECONDARY_CHANNEL_DENIED.format(
            key=key, name=name, channel_id=channel_id))]

    privileges = getattr(member, "privileges", None)
    if getattr(member, "status", None) not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return [(FAIL, MSG_PREFLIGHT_SECONDARY_NOT_ADMIN.format(key=key, name=name))]
    if getattr(privileges, "can_post_messages", None) is False:
        try:
            if await _timed(_is_broadcast_target(client, member, channel_id)):
                return [(FAIL, MSG_PREFLIGHT_SECONDARY_POST_DISABLED.format(key=key, name=name))]
        except Exception as e:
            return [(WARN, f"⚠ {key} {name}: posting right could not be checked "
                           f"({_describe(e, 'Telegram')}).")]

    try:
        await _timed(_can_read_history(client, channel_id))
    except Exception as e:
        if isinstance(e, (TypeError, AttributeError)):
            logger.error(f"Preflight read check misused the client: {e}", exc_info=True)
            return [(WARN, f"⚠ {key} {name}: history could not be checked "
                           "(the check itself failed; see the log).")]
        if not is_transient(e):
            return [(FAIL, MSG_PREFLIGHT_SECONDARY_UNREADABLE.format(key=key, name=name))]
        return [(WARN, f"⚠ {key} {name}: history read could not be checked "
                       f"({_describe(e, 'Telegram')}).")]
    return [(OK, f"{key} {name}: administrator, can post, can read")]


# -- database -------------------------------------------------------------------

async def check_database(database) -> List[Result]:
    try:
        await _timed(database.ping())
    except ServerSelectionTimeoutError as e:
        return [(WARN, MSG_PREFLIGHT_DB_TIMEOUT.format(reason=_describe(e)))]
    except OperationFailure as e:
        return [(FAIL, MSG_PREFLIGHT_DB_LOGIN_REJECTED.format(reason=_describe(e)))]
    except ConfigurationError as e:
        return [(FAIL, MSG_PREFLIGHT_DB_MALFORMED_URL.format(reason=_describe(e)))]
    except Exception as e:
        if is_transient(e):
            return [(WARN, f"⚠ DATABASE_URL: {_describe(e)}.")]
        return [(FAIL, f"✖ DATABASE_URL: {_describe(e)}.")]

    try:
        await _timed(database.verify_writable())
    except OperationFailure as e:
        code = getattr(e, "code", None)
        if code == 13:
            return [(FAIL, MSG_PREFLIGHT_DB_READ_ONLY)]
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
        return [(WARN, MSG_PREFLIGHT_SHORTENER_MISSING_CREDS)]

    from Thunder.utils.shortener import shorten

    sample = "https://t.me/thunder_untested"
    try:
        result = await _timed(shorten(sample), seconds=15)
    except Exception as e:
        return [(WARN, f"⚠ URL_SHORTENER_SITE: check failed ({_describe(e, 'the provider')}).")]
    if not result or result == sample:
        return [(WARN, MSG_PREFLIGHT_SHORTENER_UNCHANGED)]
    return [(OK, f"URL shortener {site} working")]


async def check_public_url(url: str, expected_username: Optional[str] = None) -> List[Result]:
    """Distinguish unreachable, non-JSON, and foreign /status replies."""
    target = url.rstrip("/") + "/status"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(target) as response:
                if response.status != 200:
                    return [(WARN, MSG_PREFLIGHT_URL_BAD_STATUS.format(
                        url=url, status=response.status))]
                payload = await response.json(content_type=None)
    except aiohttp.ContentTypeError:
        return [(WARN, MSG_PREFLIGHT_URL_NON_JSON.format(url=url))]
    except Exception as e:
        return [(WARN, MSG_PREFLIGHT_URL_UNREACHABLE.format(url=url, reason=type(e).__name__))]

    if not isinstance(payload, dict) or not isinstance(payload.get("server"), dict):
        return [(WARN, MSG_PREFLIGHT_URL_FOREIGN_REPLY.format(url=url))]
    if expected_username:
        telegram = payload.get("telegram_bot")
        actual = telegram.get("username") if isinstance(telegram, dict) else None
        if not isinstance(actual, str) or actual.lstrip("@").casefold() != expected_username.lstrip("@").casefold():
            return [(WARN, MSG_PREFLIGHT_URL_FOREIGN_REPLY.format(url=url))]
    return [(OK, f"Public URL {url} serving")]
