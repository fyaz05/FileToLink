import functools
import secrets

from pyrogram import Client, enums, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from Thunder.bot import StreamBot
from Thunder.utils.broadcast import broadcast_ids
from Thunder.utils.commands import build_help_text
from Thunder.utils.decorators import owner_only
from Thunder.utils.force_channel import get_force_info
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_ABOUT,
    MSG_BROADCAST_CANCEL,
    MSG_BUTTON_ABOUT,
    MSG_BUTTON_CLOSE,
    MSG_BUTTON_GET_HELP,
    MSG_BUTTON_GITHUB,
    MSG_BUTTON_JOIN_CHANNEL,
    MSG_ERROR_BROADCAST_INSTRUCTION,
    MSG_ERROR_BROADCAST_RESTART,
    MSG_ERROR_CALLBACK_UNSUPPORTED,
    MSG_ERROR_CLOSE_NOT_ALLOWED,
    MSG_ERROR_UNEXPECTED,
)
from Thunder.utils.safe_call import answer_safe, delete_safe, edit_safe
from Thunder.vars import Var


def guard_callback(fn):
    """Panic isolation for callbacks; answers query so no stale spinner."""

    @functools.wraps(fn)
    async def wrapper(client: Client, callback_query: CallbackQuery):
        try:
            return await fn(client, callback_query)
        except Exception as e:
            error_id = secrets.token_hex(6)
            logger.error(f"Callback error {error_id} in {fn.__name__}: {e}", exc_info=True)
            try:
                await answer_safe(callback_query, MSG_ERROR_UNEXPECTED, show_alert=True)
            except Exception:
                pass
            try:
                from Thunder.utils.bot_utils import notify_own
                from Thunder.utils.messages import MSG_CRITICAL_ERROR

                await notify_own(
                    client,
                    MSG_CRITICAL_ERROR.format(
                        error=f"callback:{fn.__name__}: {e}", error_id=error_id
                    ),
                )
            except Exception:
                logger.debug("Owner notification for callback failure also failed", exc_info=True)

    return wrapper


async def get_force_channel_button(client: Client):
    if not Var.FORCE_CHANNEL_ID:
        return None
    try:
        link, title = await get_force_info(client)
        if link:
            return [
                InlineKeyboardButton(
                    MSG_BUTTON_JOIN_CHANNEL.format(channel_title=title or "Channel"),
                    url=link,
                )
            ]
    except Exception as e:
        logger.error(f"Error getting force channel button: {e}", exc_info=True)
    return None


@StreamBot.on_callback_query(filters.regex(r"^help_command$"))
@guard_callback
async def help_callback(client: Client, callback_query: CallbackQuery):
    await answer_safe(callback_query)
    buttons = [[InlineKeyboardButton(MSG_BUTTON_ABOUT, callback_data="about_command")]]
    force_button = await get_force_channel_button(client)
    if force_button:
        buttons.append(force_button)
    buttons.append([InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")])
    help_text = build_help_text(Var.MAX_BATCH_FILES)
    try:
        await edit_safe(
            callback_query.message,
            help_text,
            reply_markup=InlineKeyboardMarkup(buttons),  # type: ignore[arg-type]
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.debug(f"Could not edit help panel: {e}")


@StreamBot.on_callback_query(filters.regex(r"^about_command$"))
@guard_callback
async def about_callback(client: Client, callback_query: CallbackQuery):
    await answer_safe(callback_query)
    buttons = [
        [InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command")],
        [
            InlineKeyboardButton(MSG_BUTTON_GITHUB, url="https://github.com/fyaz05/FileToLink"),
            InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel"),
        ],
    ]
    try:
        await edit_safe(
            callback_query.message,
            MSG_ABOUT,
            reply_markup=InlineKeyboardMarkup(buttons),  # type: ignore[arg-type]
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.debug(f"Could not edit about panel: {e}")


@StreamBot.on_callback_query(filters.regex(r"^restart_broadcast$"))
@guard_callback
async def restart_broadcast_callback(client: Client, callback_query: CallbackQuery):
    if not await owner_only(client, callback_query):
        return
    await answer_safe(callback_query, MSG_ERROR_BROADCAST_RESTART, show_alert=True)
    buttons = [
        [
            InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command"),
            InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel"),
        ]
    ]
    try:
        await edit_safe(
            callback_query.message,
            MSG_ERROR_BROADCAST_INSTRUCTION,
            reply_markup=InlineKeyboardMarkup(buttons),  # type: ignore[arg-type]
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.debug(f"Could not edit restart-broadcast panel: {e}")


@StreamBot.on_callback_query(filters.regex(r"^close_panel$"))
@guard_callback
async def close_panel_callback(client: Client, callback_query: CallbackQuery):
    closer_id = callback_query.from_user.id if callback_query.from_user else None
    message = callback_query.message

    is_allowed = False
    if closer_id == Var.OWNER_ID:
        is_allowed = True
    elif closer_id is not None and message is not None:
        if message.reply_to_message and message.reply_to_message.from_user:
            is_allowed = closer_id == message.reply_to_message.from_user.id
        if not is_allowed and message.chat and message.chat.type == enums.ChatType.PRIVATE:
            is_allowed = closer_id == message.chat.id

    if not is_allowed:
        await answer_safe(callback_query, MSG_ERROR_CLOSE_NOT_ALLOWED, show_alert=True)
        return

    await answer_safe(callback_query)
    if message:
        try:
            await delete_safe(message)
        except Exception as e:
            logger.debug(
                f"Failed to delete callback query message {getattr(message, 'id', '?')}: {e}"
            )


@StreamBot.on_callback_query(filters.regex(r"^cancel_"))
@guard_callback
async def cancel_broadcast(client: Client, callback_query: CallbackQuery):
    if not await owner_only(client, callback_query):
        return
    raw = callback_query.data or ""
    if isinstance(raw, bytes):
        # stub-typed str|bytes|None; decode defensively, never crash the panel
        raw = raw.decode("utf-8", errors="replace")
    parts = raw.split("_", 1)
    broadcast_id = parts[1] if len(parts) > 1 else ""
    entry = broadcast_ids.get(broadcast_id)
    if entry is not None:
        entry["cancelled"] = True
        try:
            await edit_safe(
                callback_query.message, MSG_BROADCAST_CANCEL.format(broadcast_id=broadcast_id)
            )
        except Exception as e:
            logger.debug(f"Could not edit cancel panel: {e}")
    else:
        await answer_safe(
            callback_query, MSG_BROADCAST_CANCEL.format(broadcast_id=broadcast_id), show_alert=True
        )


@StreamBot.on_callback_query()
@guard_callback
async def fallback_callback(client: Client, callback_query: CallbackQuery):
    """Catch-all for unknown/stale buttons."""
    await answer_safe(callback_query, MSG_ERROR_CALLBACK_UNSUPPORTED, show_alert=True)
