import pytdbot
from pytdbot import types

from Thunder.bot import StreamBot
from Thunder.utils.broadcast import broadcast_ids
from Thunder.utils.compat import Filters
from Thunder.utils.decorators import owner_only
from Thunder.utils.logger import logger
from Thunder.utils.telegram_helpers import is_error
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
    MSG_HELP,
)
from Thunder.vars import Var


async def get_force_channel_button(client: pytdbot.Client):
    if not Var.FORCE_CHANNEL_ID:
        return None
    try:
        chat = await client.getChat(chat_id=Var.FORCE_CHANNEL_ID)
        if isinstance(chat, types.Error):
            return None
        if chat:
            invite_link = None
            if hasattr(chat, "invite_link") and chat.invite_link:
                invite_link = chat.invite_link
            if not invite_link:
                if hasattr(chat, "type") and isinstance(chat.type, types.ChatTypeSupergroup):
                    sg_id = chat.type.supergroup_id
                    invite_link = f"https://t.me/c/{sg_id}"
            if invite_link:
                return [types.InlineKeyboardButton(
                    text=MSG_BUTTON_JOIN_CHANNEL.format(channel_title=chat.title or "Channel"),
                    type=types.InlineKeyboardButtonTypeUrl(url=invite_link)
                )]
    except Exception as e:
        logger.error(f"Error getting force channel button: {e}", exc_info=True)
    return None


def _cb_data(callback_query: types.UpdateNewCallbackQuery) -> str:
    payload = callback_query.payload
    if isinstance(payload, types.CallbackQueryPayloadData):
        return payload.data.decode("utf-8", errors="replace")
    return ""


async def _edit_cb_message(client: pytdbot.Client, cq: types.UpdateNewCallbackQuery, text: str, reply_markup=None):
    return await client.editTextMessage(
        chat_id=cq.chat_id,
        message_id=cq.message_id,
        text=text,
        reply_markup=reply_markup,
    )


async def _delete_cb_message(client: pytdbot.Client, cq: types.UpdateNewCallbackQuery):
    return await client.deleteMessages(
        chat_id=cq.chat_id,
        message_ids=[cq.message_id],
        revoke=True,
    )


@StreamBot.on_updateNewCallbackQuery(filters=Filters.regex(r"^help_command$"))
async def help_callback(client: pytdbot.Client, callback_query: types.UpdateNewCallbackQuery):
    try:
        result = await callback_query.answer(text="", show_alert=False)
        if is_error(result):
            logger.debug(f"Callback answer failed: {result.message}")
        buttons = [[types.InlineKeyboardButton(
            text=MSG_BUTTON_ABOUT,
            type=types.InlineKeyboardButtonTypeCallback(data=b"about_command")
        )]]
        force_button = await get_force_channel_button(client)
        if force_button:
            buttons.append(force_button)
        buttons.append([types.InlineKeyboardButton(
            text=MSG_BUTTON_CLOSE,
            type=types.InlineKeyboardButtonTypeCallback(data=b"close_panel")
        )])
        try:
            await _edit_cb_message(
                client, callback_query,
                text=MSG_HELP.format(max_files=Var.MAX_BATCH_FILES),
                reply_markup=types.ReplyMarkupInlineKeyboard(rows=buttons)
            )
        except Exception as e:
            logger.error(f"Error editing help message: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in help callback: {e}", exc_info=True)


@StreamBot.on_updateNewCallbackQuery(filters=Filters.regex(r"^about_command$"))
async def about_callback(client: pytdbot.Client, callback_query: types.UpdateNewCallbackQuery):
    try:
        result = await callback_query.answer(text="", show_alert=False)
        if is_error(result):
            logger.debug(f"Callback answer failed: {result.message}")
        buttons = [
            [types.InlineKeyboardButton(
                text=MSG_BUTTON_GET_HELP,
                type=types.InlineKeyboardButtonTypeCallback(data=b"help_command")
            )],
            [
                types.InlineKeyboardButton(
                    text=MSG_BUTTON_GITHUB,
                    type=types.InlineKeyboardButtonTypeUrl(url="https://github.com/fyaz05/FileToLink")
                ),
                types.InlineKeyboardButton(
                    text=MSG_BUTTON_CLOSE,
                    type=types.InlineKeyboardButtonTypeCallback(data=b"close_panel")
                )
            ]
        ]
        try:
            await _edit_cb_message(
                client, callback_query,
                text=MSG_ABOUT,
                reply_markup=types.ReplyMarkupInlineKeyboard(rows=buttons)
            )
        except Exception as e:
            logger.error(f"Error editing about message: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in about callback: {e}", exc_info=True)


@StreamBot.on_updateNewCallbackQuery(filters=Filters.regex(r"^restart_broadcast$"))
async def restart_broadcast_callback(client: pytdbot.Client, callback_query: types.UpdateNewCallbackQuery):
    if not await owner_only(client, callback_query):
        return
    try:
        result = await callback_query.answer(text=MSG_ERROR_BROADCAST_RESTART, show_alert=True)
        if is_error(result):
            logger.debug(f"Callback answer failed: {result.message}")
        buttons = [
            [
                types.InlineKeyboardButton(
                    text=MSG_BUTTON_GET_HELP,
                    type=types.InlineKeyboardButtonTypeCallback(data=b"help_command")
                ),
                types.InlineKeyboardButton(
                    text=MSG_BUTTON_CLOSE,
                    type=types.InlineKeyboardButtonTypeCallback(data=b"close_panel")
                )
            ]
        ]
        try:
            await _edit_cb_message(
                client, callback_query,
                text=MSG_ERROR_BROADCAST_INSTRUCTION,
                reply_markup=types.ReplyMarkupInlineKeyboard(rows=buttons)
            )
        except Exception as e:
            logger.error(f"Error editing broadcast restart message: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in restart broadcast callback: {e}", exc_info=True)


@StreamBot.on_updateNewCallbackQuery(filters=Filters.regex(r"^close_panel$"))
async def close_panel_callback(client: pytdbot.Client, callback_query: types.UpdateNewCallbackQuery):
    try:
        result = await callback_query.answer(text="", show_alert=False)
        if is_error(result):
            logger.debug(f"Callback answer failed: {result.message}")
        try:
            await _delete_cb_message(client, callback_query)
        except Exception as e:
            logger.debug(f"Failed to delete callback message: {e}")
    except Exception as e:
        logger.error(f"General error in close panel callback: {e}", exc_info=True)


@StreamBot.on_updateNewCallbackQuery(filters=Filters.regex(r"^cancel_"))
async def cancel_broadcast(client: pytdbot.Client, callback_query: types.UpdateNewCallbackQuery):
    try:
        data = _cb_data(callback_query)
        broadcast_id = data.split("_")[1] if "_" in data else ""
        if broadcast_id in broadcast_ids:
            broadcast_ids[broadcast_id]["cancelled"] = True
            await callback_query.answer(text="Broadcast cancelled.", show_alert=False)
            try:
                await _edit_cb_message(
                    client, callback_query,
                    text=MSG_BROADCAST_CANCEL.format(broadcast_id=broadcast_id)
                )
            except Exception as e:
                logger.error(f"Error editing cancel message: {e}", exc_info=True)
        else:
            result = await callback_query.answer(text=MSG_BROADCAST_CANCEL.format(broadcast_id=broadcast_id), show_alert=True)
            if is_error(result):
                logger.debug(f"Callback answer failed: {result.message}")
    except Exception as e:
        logger.error(f"Error in cancel broadcast callback: {e}", exc_info=True)


@StreamBot.on_updateNewCallbackQuery()
async def fallback_callback(client: pytdbot.Client, callback_query: types.UpdateNewCallbackQuery):
    try:
        result = await callback_query.answer(text=MSG_ERROR_CALLBACK_UNSUPPORTED, show_alert=True)
        if is_error(result):
            logger.debug(f"Callback answer failed: {result.message}")
    except Exception as e:
        logger.error(f"Error in fallback callback: {e}", exc_info=True)
