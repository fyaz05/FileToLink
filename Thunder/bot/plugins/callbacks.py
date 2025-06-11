import uuid
import asyncio

from pyrogram import Client, filters, StopPropagation
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message
from pyrogram.errors import MessageNotModified, FloodWait

from Thunder.bot import StreamBot
from Thunder.vars import Var
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_ERROR_GENERIC_CALLBACK,
    MSG_BUTTON_JOIN_CHANNEL,
    MSG_BUTTON_ABOUT,
    MSG_HELP,
    MSG_BUTTON_CLOSE,
    MSG_BUTTON_GET_HELP,
    MSG_BUTTON_GITHUB,
    MSG_ABOUT,
    MSG_ERROR_BROADCAST_RESTART,
    MSG_ERROR_BROADCAST_INSTRUCTION,
    MSG_ERROR_CALLBACK_UNSUPPORTED
)
from Thunder.utils.decorators import owner_only
# Removed: from Thunder.utils.retry_api import retry_get_chat

async def exec_cb_cmd(cli: Client, cb_qry: CallbackQuery, cmd_name: str, cmd_fn):
    try:
        await cb_qry.answer()
    except FloodWait as e:
        logger.warning(f"FloodWait: exec_cb_cmd answer for {cmd_name}. Sleep {e.value}s")
        await asyncio.sleep(e.value)
    except Exception as e:
        logger.error(f"Error: exec_cb_cmd answer for {cmd_name}: {e}")

    if cb_qry.message:
        cb_msg = cb_qry.message
        if cb_qry.from_user:
            cb_msg.from_user = cb_qry.from_user
        else:
            logger.warning(f"exec_cb_cmd: cb_qry.from_user is None for command {cmd_name}. Using message.from_user if available.")
            if not cb_msg.from_user:
                logger.error(f"exec_cb_cmd: Cannot determine user for command {cmd_name} from callback.")
                return

        cb_msg.text = f"/{cmd_name}"
        cb_msg.command = [cmd_name]
        await cmd_fn(cli, cb_msg)
    else:
        logger.error(f"exec_cb_cmd called with CallbackQuery that has no associated message. Callback data: {cb_qry.data}")

async def handle_callback_error(callback_query: CallbackQuery, error: Exception, operation: str ="callback"): # Added type hints
    error_id = uuid.uuid4().hex[:8]
    logger.error(f"Error in {operation} (cb_data: {callback_query.data if callback_query else 'N/A'}): {error}")
    if callback_query and hasattr(callback_query, 'answer'): # Check if cb_qry can answer
        try:
            await callback_query.answer(
                MSG_ERROR_GENERIC_CALLBACK.format(error_id=error_id),
                show_alert=True
            )
        except Exception as e_ans:
            logger.error(f"Failed to answer callback query during error handling for {operation}: {e_ans}")

async def get_force_channel_button(client: Client): # Added type hint
    if not Var.FORCE_CHANNEL_ID:
        return None
    try:
        chat = await client.get_chat(Var.FORCE_CHANNEL_ID) # Direct call
        if not chat:
            logger.warning(f"Could not get chat for FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID} (returned None)")
            return None
        invite_link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else None)
        if invite_link:
            return [InlineKeyboardButton(
                MSG_BUTTON_JOIN_CHANNEL.format(channel_title=chat.title or "Channel"),
                url=invite_link
            )]
        else:
            logger.warning(f"Could not construct invite link for FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID} (Chat: {chat.title or 'N/A'})")
            return None
    except FloodWait as e_fw:
        logger.warning(f"FloodWait in get_force_channel_button for chat {Var.FORCE_CHANNEL_ID}: {e_fw}. Sleeping {e_fw.value}s")
        await asyncio.sleep(e_fw.value)
        return None # No retry for this getter, just return None
    except Exception as e:
        logger.error(f"Error creating force channel button for chat {Var.FORCE_CHANNEL_ID}: {e}")
        return None

@StreamBot.on_callback_query(filters.regex(r"^help_command$"))
async def help_callback(client: Client, callback_query: CallbackQuery):
    try:
        await callback_query.answer()
        if not callback_query.message: # Guard added
            logger.warning("help_callback: callback_query.message is None. Cannot edit.")
            return

        buttons = [[InlineKeyboardButton(MSG_BUTTON_ABOUT, callback_data="about_command")]]
        force_button = await get_force_channel_button(client)
        if force_button:
            buttons.append(force_button)
        buttons.append([InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")])

        await callback_query.message.edit_text(
            text=MSG_HELP,
            reply_markup=InlineKeyboardMarkup(buttons),
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
        if callback_query.from_user: # Guard from_user for logging
            logger.debug(f"User {callback_query.from_user.id} accessed help panel.")
    except MessageNotModified:
        if callback_query.from_user: # Guard from_user for logging
            logger.debug(f"Help panel already displayed for user {callback_query.from_user.id}")
    except Exception as e:
        await handle_callback_error(callback_query, e, "help_callback")
    finally:
        raise StopPropagation

@StreamBot.on_callback_query(filters.regex(r"^about_command$"))
async def about_callback(client: Client, callback_query: CallbackQuery):
    try:
        await callback_query.answer()
        if not callback_query.message: # Guard added
            logger.warning("about_callback: callback_query.message is None. Cannot edit.")
            return

        buttons = [
            [InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command")],
            [InlineKeyboardButton(MSG_BUTTON_GITHUB, url="https://github.com/fyaz05/FileToLink"),
             InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]
        ]
        await callback_query.message.edit_text(
            text=MSG_ABOUT,
            reply_markup=InlineKeyboardMarkup(buttons),
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
        if callback_query.from_user: # Guard from_user for logging
            logger.debug(f"User {callback_query.from_user.id} accessed about panel.")
    except MessageNotModified:
        if callback_query.from_user: # Guard from_user for logging
            logger.debug(f"About panel already displayed for user {callback_query.from_user.id}")
    except Exception as e:
        await handle_callback_error(callback_query, e, "about_callback")
    finally:
        raise StopPropagation

@StreamBot.on_callback_query(filters.regex(r"^restart_broadcast$"))
@owner_only
async def restart_broadcast_callback(client: Client, callback_query: CallbackQuery):
    try:
        await callback_query.answer(MSG_ERROR_BROADCAST_RESTART, show_alert=True)
        buttons = [
            [InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command"),
             InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]
        ]
        if callback_query.message:
            await callback_query.message.edit_text(
                MSG_ERROR_BROADCAST_INSTRUCTION,
                reply_markup=InlineKeyboardMarkup(buttons),
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
        if callback_query.from_user: # Guard from_user for logging
            logger.debug(f"User {callback_query.from_user.id} viewed broadcast restart instruction.")
    except Exception as e:
        await handle_callback_error(callback_query, e, "restart_broadcast_callback")
    finally:
        raise StopPropagation

@StreamBot.on_callback_query(filters.regex(r"^close_panel$"))
async def close_panel_callback(client: Client, callback_query: CallbackQuery):
    try:
        await callback_query.answer()
        if callback_query.message:
            await callback_query.message.delete()
            if callback_query.from_user: # Guard from_user for logging
                logger.debug(f"User {callback_query.from_user.id} closed panel")
            if callback_query.message.reply_to_message:
                try:
                    ctx = callback_query.message.reply_to_message
                    await ctx.delete()
                    if ctx.reply_to_message:
                        await ctx.reply_to_message.delete()
                except Exception as e:
                    logger.warning(f"Error deleting command messages: {e}")
    except Exception as e:
        await handle_callback_error(callback_query, e, "close_panel_callback")
    finally:
        raise StopPropagation

@StreamBot.on_callback_query(group=999)
async def fallback_callback(client: Client, callback_query: CallbackQuery):
    try:
        user_info = "Unknown User"
        if callback_query.from_user and hasattr(callback_query.from_user, 'id'):
            user_info = f"user {callback_query.from_user.id}"
        logger.debug(f"Unhandled callback query: {callback_query.data if callback_query else 'N/A'} from {user_info}")
        if callback_query and hasattr(callback_query, 'answer'):
            await callback_query.answer(MSG_ERROR_CALLBACK_UNSUPPORTED, show_alert=True)
    except Exception as e:
        logger.error(f"Error in fallback_callback answering: {e}")
    finally:
        raise StopPropagation
