# Thunder/utils/decorators.py

"""Access gates (plan H7 + M12).

One preflight chain replaces the three ad-hoc per-plugin gate orders.
Documented ordering (see AGENTS.md):

    banned -> private-mode -> token-activation -> force-sub -> shortener-status

* owner bypasses everything; authorized users bypass everything but the
  ban check;
* /start runs only ``banned + private-mode`` so the activation flow stays
  reachable;
* every DB-backed gate is cached (``flag_cache``) and **fail-closed**:
  a Mongo outage denies access with a temporary-error message instead of
  silently letting everyone through.
"""

import html
from urllib.parse import quote_plus

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from Thunder.utils.database import db
from Thunder.utils.flag_cache import flags
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_DECORATOR_BANNED,
    MSG_ERROR_ANONYMOUS_SENDER,
    MSG_ERROR_TEMP,
    MSG_ERROR_TOKEN_LINK_FAILED,
    MSG_ERROR_UNAUTHORIZED,
    MSG_ERROR_UNEXPECTED,
    MSG_PRIVATE_MODE_DENIED,
    MSG_TOKEN_INVALID,
)
from Thunder.utils.safe_call import answer_safe, reply_safe, tg_call
from Thunder.utils.shortener import shorten
from Thunder.utils.tokens import allowed, check, generate
from Thunder.vars import Var


async def check_banned(client, message: Message) -> bool:
    """Ban gate -- cached, fail-closed (H7)."""
    try:
        if not message.from_user:
            return True
        user_id = message.from_user.id
        if user_id == Var.OWNER_ID:
            return True

        try:
            ban_details = await flags.get_or_load(
                ("banned_user", user_id),
                # raise_on_error=True: the DB method otherwise swallows Mongo
                # outages into None, which the cache would treat as
                # "not banned" (negative-cached for 5 min) -- fail-open.
                lambda: db.is_user_banned(user_id, raise_on_error=True),
            )
        except Exception as e:
            # fail-closed: a Mongo outage must not un-ban everybody
            logger.error(f"Ban check degraded for user {user_id}: {e}", exc_info=True)
            try:
                await reply_safe(message, MSG_ERROR_TEMP)
            except Exception:
                pass
            return False

        if ban_details:
            banned_at = ban_details.get("banned_at")
            ban_time = (
                banned_at.strftime("%B %d, %Y, %I:%M %p UTC")
                if banned_at and hasattr(banned_at, "strftime")
                else str(banned_at)
                if banned_at
                else "N/A"
            )
            try:
                await reply_safe(
                    message,
                    MSG_DECORATOR_BANNED.format(
                        # reason is owner-set free text; escape so the
                        # DEFAULT parse pass cannot reflow it into markup
                        reason=html.escape(ban_details.get("reason", "Not specified")),
                        ban_time=ban_time,
                    ),
                )
            except Exception:
                pass
            logger.debug(f"Blocked banned user {user_id}.")
            return False
        return True
    except Exception as e:
        logger.error(f"Error in check_banned: {e}", exc_info=True)
        return False


async def check_private_mode(client, message: Message) -> bool:
    """PRIVATE_MODE allowlist gate (M12): owner + authorized users only."""
    if not Var.PRIVATE_MODE:
        return True
    if not message.from_user:
        # Channel-posted / anonymous-admin messages have no verifiable user
        # id, so allowlist membership cannot be checked: fail-closed.
        logger.debug("Rejected unattributable sender (PRIVATE_MODE, no from_user).")
        try:
            await reply_safe(message, MSG_PRIVATE_MODE_DENIED)
        except Exception:
            pass
        return False
    user_id = message.from_user.id
    if user_id == Var.OWNER_ID:
        return True
    try:
        if await allowed(user_id):
            return True
    except Exception as e:
        logger.error(f"Private-mode auth check failed for {user_id}: {e}", exc_info=True)
        try:
            await reply_safe(message, MSG_ERROR_TEMP)
        except Exception:
            pass
        return False
    try:
        await reply_safe(message, MSG_PRIVATE_MODE_DENIED)
    except Exception:
        pass
    logger.debug(f"Rejected non-allowlisted user {user_id} (PRIVATE_MODE).")
    return False


async def require_token(client, message: Message) -> bool:
    """Token-activation gate (H7: cached checks, fail-closed)."""
    try:
        # NOTE: the TOKEN_ENABLED short-circuit comes FIRST -- when the
        # feature is off this gate must be a no-op even for anonymous
        # senders, or group /link via an anonymous admin would break.
        if not Var.TOKEN_ENABLED:
            return True

        if not message.from_user:
            # Channel-posted / anonymous senders cannot hold an activation
            # token: fail-closed (they also cannot complete the flow).
            logger.debug("Denied unattributable sender (token gate, no from_user).")
            try:
                await reply_safe(message, MSG_ERROR_ANONYMOUS_SENDER)
            except Exception:
                pass
            return False

        user_id = message.from_user.id
        if user_id == Var.OWNER_ID:
            return True

        try:
            if await allowed(user_id) or await check(user_id):
                return True
        except Exception as e:
            logger.error(f"Token gate degraded for user {user_id}: {e}", exc_info=True)
            try:
                await reply_safe(message, MSG_ERROR_TEMP)
            except Exception:
                pass
            return False

        try:
            temp_token_string = await generate(user_id)
        except Exception as e:
            logger.error(
                f"Failed to generate temporary token for user {user_id}: {e}", exc_info=True
            )
            try:
                await reply_safe(message, MSG_ERROR_TOKEN_LINK_FAILED)
            except Exception:
                pass
            return False

        if not temp_token_string:
            logger.error(
                f"Temporary token generation returned empty for user {user_id}.", exc_info=True
            )
            try:
                await reply_safe(message, MSG_ERROR_TOKEN_LINK_FAILED)
            except Exception:
                pass
            return False

        try:
            me = await tg_call(client.get_me)
        except Exception as e:
            logger.error(f"Failed to get bot info for user {user_id}: {e}", exc_info=True)
            try:
                await reply_safe(message, MSG_ERROR_UNEXPECTED)
            except Exception:
                pass
            return False
        if not me:
            logger.error(f"get_me returned nothing for user {user_id}.", exc_info=True)
            try:
                await reply_safe(message, MSG_ERROR_UNEXPECTED)
            except Exception:
                pass
            return False
        deep_link = (
            "https://t.me/" + me.username + "?start=" + quote_plus(temp_token_string, safe="")
        )
        short_url = deep_link

        try:
            short_url_result = await shorten(deep_link)
            if short_url_result:
                short_url = short_url_result
        except Exception as e:
            logger.warning(
                f"Failed to shorten token link for user {user_id}: {e}. Using full link.",
                exc_info=True,
            )

        try:
            await reply_safe(
                message,
                MSG_TOKEN_INVALID,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Activate Access", url=short_url)]]
                ),
            )
        except Exception:
            pass
        logger.debug(f"Sent temporary token activation link to user {user_id}.")
        return False
    except Exception as e:
        logger.error(f"Error in require_token: {e}", exc_info=True)
        try:
            await reply_safe(message, MSG_ERROR_UNEXPECTED)
        except Exception as inner_e:
            logger.error(
                f"Failed to send error message to user in require_token: {inner_e}", exc_info=True
            )
        return False


async def get_shortener_status(client, message: Message) -> bool:
    try:
        user_id = message.from_user.id if message.from_user else None
        use_shortener = Var.SHORTEN_MEDIA_LINKS
        if user_id:
            try:
                if user_id == Var.OWNER_ID or await allowed(user_id):
                    use_shortener = False
            except Exception as e:
                logger.warning(
                    f"Error checking allowed status for user {user_id}: {e}. Defaulting shortener behavior.",
                    exc_info=True,
                )
        return use_shortener
    except Exception as e:
        logger.error(f"Error in get_shortener_status: {e}", exc_info=True)
        return Var.SHORTEN_MEDIA_LINKS


# --------------------------------------------------------------------------
# M12: unified preflight chain
# --------------------------------------------------------------------------

#: gate registry -- order is the documented contract; adding a new gate is a
#: one-place change here (chain asserted by tests/test_unit/test_preflight.py).
PREFLIGHT_GATES = {
    "banned": check_banned,
    "private_mode": check_private_mode,
    "token": require_token,
}

#: preset gate chains -- the documented orders, so callers cannot invent
#: their own sequence and a new command cannot forget a gate
GATES_STANDARD: tuple = ("banned", "private_mode", "token")
GATES_START: tuple = ("banned", "private_mode")
GATES_INFO: tuple = ("banned",)


async def preflight(
    client,
    message: Message,
    *,
    gates: tuple = GATES_STANDARD,
) -> bool | None:
    """Run the standard gate chain in order.

    Returns the final shortener status (last gate's value convention) or
    ``None`` when any gate rejects the request.  Unknown gate ids REJECT
    (fail-closed) -- a typo'd id must never silently disable a check.
    """
    for name in gates:
        gate = PREFLIGHT_GATES.get(name)
        if gate is None:
            logger.error(f"preflight: unknown gate {name!r}; rejecting request (fail-closed)")
            return None
        if not await gate(client, message):
            return None
    return await get_shortener_status(client, message)


async def force_sub_gate(client, message: Message) -> bool:
    """Force-subscribe gate, kept separate so the rate-limit chain can slot
    it after the token gate (called explicitly by stream entries)."""
    from Thunder.utils.force_channel import force_channel_check

    return await force_channel_check(client, message)


async def owner_only(client, update) -> bool:
    try:
        user = None
        if hasattr(update, "from_user"):
            user = update.from_user
        else:
            logger.error(
                f"Unsupported update type or missing from_user in owner_only: {type(update)}",
                exc_info=True,
            )
            return False

        if not user or user.id != Var.OWNER_ID:
            if hasattr(update, "answer"):
                await answer_safe(update, MSG_ERROR_UNAUTHORIZED, show_alert=True)
            logger.warning(
                f"Unauthorized access attempt by {user.id if user else 'unknown'} to owner_only function."
            )
            return False

        return True
    except Exception as e:
        logger.error(f"Error in owner_only: {e}", exc_info=True)
        try:
            if hasattr(update, "answer"):
                await answer_safe(update, MSG_ERROR_UNEXPECTED, show_alert=True)
        except Exception as inner_e:
            logger.error(f"Failed to send error answer in owner_only: {inner_e}", exc_info=True)
        return False
