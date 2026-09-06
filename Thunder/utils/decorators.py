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

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from Thunder.utils.database import db
from Thunder.utils.flag_cache import flags
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_DECORATOR_BANNED,
    MSG_ERROR_TEMP,
    MSG_ERROR_UNAUTHORIZED,
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
                lambda: db.is_user_banned(user_id),
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
                        reason=ban_details.get("reason", "Not specified"), ban_time=ban_time
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
    if not getattr(Var, "PRIVATE_MODE", False):
        return True
    if not message.from_user:
        return True
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
        if not message.from_user:
            return True

        if not getattr(Var, "TOKEN_ENABLED", False):
            return True

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
                await reply_safe(
                    message,
                    "Sorry, could not generate an access token link. Please try again later.",
                )
            except Exception:
                pass
            return False

        if not temp_token_string:
            logger.error(
                f"Temporary token generation returned empty for user {user_id}.", exc_info=True
            )
            try:
                await reply_safe(
                    message,
                    "Sorry, could not generate an access token link. Please try again later.",
                )
            except Exception:
                pass
            return False

        try:
            me = await tg_call(client.get_me)
        except Exception as e:
            logger.error(f"Failed to get bot info for user {user_id}: {e}", exc_info=True)
            try:
                await reply_safe(
                    message, "Sorry, an unexpected error occurred. Please try again later."
                )
            except Exception:
                pass
            return False
        if not me:
            logger.error(f"get_me returned nothing for user {user_id}.", exc_info=True)
            try:
                await reply_safe(
                    message, "Sorry, an unexpected error occurred. Please try again later."
                )
            except Exception:
                pass
            return False
        deep_link = f"https://t.me/{me.username}?start={temp_token_string}"
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
            await reply_safe(
                message, "An error occurred while checking your authorization. Please try again."
            )
        except Exception as inner_e:
            logger.error(
                f"Failed to send error message to user in require_token: {inner_e}", exc_info=True
            )
        return False


async def get_shortener_status(client, message: Message) -> bool:
    try:
        user_id = message.from_user.id if message.from_user else None
        use_shortener = getattr(Var, "SHORTEN_MEDIA_LINKS", False)
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
        return getattr(Var, "SHORTEN_MEDIA_LINKS", False)


# --------------------------------------------------------------------------
# M12: unified preflight chain
# --------------------------------------------------------------------------

#: gate registry -- order is the documented contract; adding a new gate is a
#: one-place change here (asserted by tests/test_preflight.py).
PREFLIGHT_GATES = {
    "banned": check_banned,
    "private_mode": check_private_mode,
    "token": require_token,
}


async def preflight(
    client,
    message: Message,
    *,
    gates: tuple = ("banned", "private_mode", "token"),
    skip: tuple = (),
) -> bool | None:
    """Run the standard gate chain in order.

    Returns the final shortener status (last gate's value convention) or
    ``None`` when any gate rejects the request.
    """
    for name in gates:
        if name in skip:
            continue
        gate = PREFLIGHT_GATES.get(name)
        if gate is None:
            continue
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
                await answer_safe(update, "An error occurred. Please try again.", show_alert=True)
        except Exception as inner_e:
            logger.error(f"Failed to send error answer in owner_only: {inner_e}", exc_info=True)
        return False
