# Thunder/bot/plugins/callbacks.py

import asyncio

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageNotModified, MessageDeleteForbidden
from pyrogram.types import (CallbackQuery, InlineKeyboardButton,
                            InlineKeyboardMarkup)

from Thunder.bot import StreamBot
from Thunder.utils.broadcast import broadcast_ids
from Thunder.utils.decorators import owner_only
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_ABOUT, MSG_BROADCAST_CANCEL, MSG_BUTTON_ABOUT, MSG_BUTTON_CLOSE,
    MSG_BUTTON_GET_HELP, MSG_BUTTON_GITHUB, MSG_BUTTON_JOIN_CHANNEL,
    MSG_ERROR_BROADCAST_INSTRUCTION, MSG_ERROR_BROADCAST_RESTART,
    MSG_ERROR_CALLBACK_UNSUPPORTED, MSG_HELP
)
from Thunder.vars import Var

async def get_force_channel_button(client: Client):
    if not Var.FORCE_CHANNEL_ID:
        return None
    try:
        try:
            chat = await client.get_chat(Var.FORCE_CHANNEL_ID)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            chat = await client.get_chat(Var.FORCE_CHANNEL_ID)
        if chat:
            invite_link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else None)
            if invite_link:
                return [InlineKeyboardButton(
                    MSG_BUTTON_JOIN_CHANNEL.format(channel_title=chat.title or "Channel"),
                    url=invite_link
                )]
    except Exception as e:
        logger.error(f"Error getting force channel button: {e}", exc_info=True)
    return None

@StreamBot.on_callback_query(filters.regex(r"^help_command$"))
async def help_callback(client: Client, callback_query: CallbackQuery):
    try:
        await callback_query.answer()
        buttons = [[InlineKeyboardButton(MSG_BUTTON_ABOUT, callback_data="about_command")]]
        force_button = await get_force_channel_button(client)
        if force_button:
            buttons.append(force_button)
        buttons.append([InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")])
        try:
            await callback_query.message.edit_text(
                text=MSG_HELP.format(max_files=Var.MAX_BATCH_FILES),
                reply_markup=InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True
            )
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await callback_query.message.edit_text(
                text=MSG_HELP.format(max_files=Var.MAX_BATCH_FILES),
                reply_markup=InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True
            )
    except MessageNotModified:
        pass
    except Exception as e:
        logger.error(f"Error in help callback: {e}", exc_info=True)
        try:
            await callback_query.answer("An error occurred. Please try again.", show_alert=True)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await callback_query.answer("An error occurred. Please try again.", show_alert=True)

@StreamBot.on_callback_query(filters.regex(r"^about_command$"))
async def about_callback(client: Client, callback_query: CallbackQuery):
    try:
        await callback_query.answer()
        buttons = [
            [InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command")],
            [
                InlineKeyboardButton(MSG_BUTTON_GITHUB, url="https://github.com/fyaz05/FileToLink"),
                InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")
            ]
        ]
        try:
            await callback_query.message.edit_text(
                text=MSG_ABOUT,
                reply_markup=InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True
            )
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await callback_query.message.edit_text(
                text=MSG_ABOUT,
                reply_markup=InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True
            )
    except MessageNotModified:
        pass
    except Exception as e:
        logger.error(f"Error in about callback: {e}", exc_info=True)
        try:
            await callback_query.answer("An error occurred. Please try again.", show_alert=True)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await callback_query.answer("An error occurred. Please try again.", show_alert=True)

@StreamBot.on_callback_query(filters.regex(r"^restart_broadcast$"))
async def restart_broadcast_callback(client: Client, callback_query: CallbackQuery):
    if not await owner_only(client, callback_query):
        return
    try:
        try:
            await callback_query.answer(MSG_ERROR_BROADCAST_RESTART, show_alert=True)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await callback_query.answer(MSG_ERROR_BROADCAST_RESTART, show_alert=True)
        buttons = [
            [
                InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command"),
                InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")
            ]
        ]
        try:
            await callback_query.message.edit_text(
                MSG_ERROR_BROADCAST_INSTRUCTION,
                reply_markup=InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True
            )
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await callback_query.message.edit_text(
                MSG_ERROR_BROADCAST_INSTRUCTION,
                reply_markup=InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True
            )
    except Exception as e:
        logger.error(f"Error in restart broadcast callback: {e}", exc_info=True)
        try:
            await callback_query.answer("An error occurred. Please try again.", show_alert=True)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await callback_query.answer("An error occurred. Please try again.", show_alert=True)

@StreamBot.on_callback_query(filters.regex(r"^close_panel$"))
async def close_panel_callback(client: Client, callback_query: CallbackQuery):
    try:
        try:
            await callback_query.answer()
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await callback_query.answer()
        try:
            try:
                await callback_query.message.delete()
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await callback_query.message.delete()
        except MessageDeleteForbidden:
            logger.debug(f"Failed to delete callback query message due to permissions. Message ID: {callback_query.message.id}")
        except Exception as e:
            logger.error(f"Error deleting callback query message: {e}", exc_info=True)

        if callback_query.message.reply_to_message:
            try:
                reply_msg = callback_query.message.reply_to_message
                try:
                    await reply_msg.delete()
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await reply_msg.delete()
            except MessageDeleteForbidden:
                logger.debug(f"Failed to delete replied message due to permissions. Message ID: {reply_msg.id}")
            except Exception as e:
                logger.error(f"Error deleting replied message: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"General error in close panel callback: {e}", exc_info=True)

@StreamBot.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_broadcast(client: Client, callback_query: CallbackQuery):
    try:
        broadcast_id = callback_query.data.split("_")[1]
        if broadcast_id in broadcast_ids:
            broadcast_ids[broadcast_id]["cancelled"] = True
            try:
                await callback_query.message.edit_text(
                    MSG_BROADCAST_CANCEL.format(broadcast_id=broadcast_id)
                )
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await callback_query.message.edit_text(
                    MSG_BROADCAST_CANCEL.format(broadcast_id=broadcast_id)
                )
        else:
            try:
                await callback_query.answer(
                    MSG_BROADCAST_CANCEL.format(broadcast_id=broadcast_id),
                    show_alert=True
                )
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await callback_query.answer(
                    MSG_BROADCAST_CANCEL.format(broadcast_id=broadcast_id),
                    show_alert=True
                )
    except Exception as e:
        logger.error(f"Error in cancel broadcast callback: {e}", exc_info=True)
        try:
            await callback_query.answer("An error occurred. Please try again.", show_alert=True)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await callback_query.answer("An error occurred. Please try again.", show_alert=True)

@StreamBot.on_callback_query()
async def fallback_callback(client: Client, callback_query: CallbackQuery):
    try:
        try:
            await callback_query.answer(MSG_ERROR_CALLBACK_UNSUPPORTED, show_alert=True)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await callback_query.answer(MSG_ERROR_CALLBACK_UNSUPPORTED, show_alert=True)
    except Exception as e:
        logger.error(f"Error in fallback callback: {e}", exc_info=True)
