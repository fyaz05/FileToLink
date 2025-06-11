import time
import asyncio
import uuid
from datetime import datetime # timedelta removed
from typing import Optional, Dict # Tuple removed, Dict added

# from urllib.parse import quote_plus # Removed, not used directly

from pyrogram import filters, StopPropagation
from pyrogram.client import Client
from pyrogram.errors import MessageNotModified, FloodWait # RPCError removed, FloodWait added
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
    # CallbackQuery, # Removed
    LinkPreviewOptions
)
from Thunder.bot import StreamBot
from Thunder.vars import Var
from Thunder.utils.database import db
# Specific message imports
from Thunder.utils.messages import (
    MSG_TOKEN_ACTIVATED, MSG_TOKEN_FAILED, MSG_ERROR_FILE_INVALID, MSG_LINKS,
    MSG_BUTTON_STREAM_NOW, MSG_BUTTON_DOWNLOAD, MSG_START_INVALID_PAYLOAD,
    MSG_FILE_ACCESS_ERROR, MSG_WELCOME, MSG_COMMUNITY_CHANNEL,
    MSG_BUTTON_GET_HELP, MSG_BUTTON_ABOUT, MSG_BUTTON_GITHUB, MSG_BUTTON_CLOSE,
    MSG_BUTTON_JOIN_CHANNEL, MSG_ERROR_GENERIC, MSG_HELP, MSG_ABOUT,
    MSG_DC_ANON_ERROR, MSG_BUTTON_VIEW_PROFILE, MSG_FILE_TYPE_DOCUMENT,
    MSG_FILE_TYPE_PHOTO, MSG_FILE_TYPE_VIDEO, MSG_FILE_TYPE_AUDIO,
    MSG_FILE_TYPE_VOICE, MSG_FILE_TYPE_STICKER, MSG_FILE_TYPE_ANIMATION,
    MSG_FILE_TYPE_VIDEO_NOTE, MSG_FILE_TYPE_UNKNOWN, MSG_DC_UNKNOWN,
    MSG_DC_FILE_INFO, MSG_DC_FILE_ERROR, MSG_ERROR_USER_INFO,
    MSG_DC_INVALID_USAGE, MSG_PING_START, MSG_PING_RESPONSE,
    MSG_ERROR_GENERIC_CALLBACK
    # MSG_ERROR_CALLBACK_UNSUPPORTED is used in callbacks.py, not here.
)
from Thunder.utils.human_readable import humanbytes
# Assuming minified names for file_properties as per bot_utils spec
from Thunder.utils.file_properties import get_fsize, get_fname
from Thunder.utils.force_channel import force_channel_check
from Thunder.utils.logger import logger
# from Thunder.utils.tokens import check # Removed
from Thunder.utils.decorators import check_banned
# Using minified names from bot_utils
from Thunder.utils.bot_utils import (
    # notify_channel, # Removed as unused
    log_newusr, # Minified name
    gen_links,  # Minified name
    reply_user_err, # Minified name
    gen_dc_txt, # Minified name
    get_user    # Minified name, was get_user_safely
)

def has_media(message: Message) -> bool: # Added type hint
    return any(
        hasattr(message, attr) and getattr(message, attr)
        for attr in ["document", "photo", "video", "audio", "voice",
                    "sticker", "animation", "video_note"]
    )

@check_banned
@StreamBot.on_message(filters.command("start") & filters.private)
async def start_command(bot: Client, message: Message):
    user_id = message.from_user.id if message.from_user else None
    user_id_str = str(user_id) if user_id else 'unknown'

    try:
        if message.from_user:
            await log_newusr(bot, user_id, message.from_user.first_name)

        if len(message.command) == 2:
            payload = message.command[1]
            token_doc = await db.token_col.find_one({"token": payload})

            if token_doc:
                if token_doc["user_id"] == user_id:
                    if not token_doc.get("activated", False):
                        token_expires_at = token_doc.get("expires_at")
                        if isinstance(token_expires_at, datetime) and token_expires_at > datetime.utcnow():
                            await db.token_col.update_one(
                                {"token": payload, "user_id": user_id},
                                {"$set": {"activated": True}}
                            )
                            duration_hours = getattr(Var, "TOKEN_TTL_HOURS", 24)
                            try:
                                await message.reply_text(
                                    MSG_TOKEN_ACTIVATED.format(duration_hours=duration_hours),
                                    link_preview_options=LinkPreviewOptions(is_disabled=True), quote=True )
                            except FloodWait as e_fw:
                                logger.warning(f"FloodWait in start_command (token_activated): {e_fw}. Sleeping {e_fw.value}s")
                                await asyncio.sleep(e_fw.value)
                                await message.reply_text(
                                    MSG_TOKEN_ACTIVATED.format(duration_hours=duration_hours),
                                    link_preview_options=LinkPreviewOptions(is_disabled=True), quote=True )
                        else:
                            await message.reply_text(
                                MSG_TOKEN_FAILED.format(reason="This activation link has expired.", error_id=uuid.uuid4().hex[:8]),
                                link_preview_options=LinkPreviewOptions(is_disabled=True), quote=True )
                    else:
                        await message.reply_text(
                            MSG_TOKEN_FAILED.format(reason="Token has already been activated.", error_id=uuid.uuid4().hex[:8]),
                            link_preview_options=LinkPreviewOptions(is_disabled=True), quote=True )
                else:
                    logger.warning(f"User {user_id} attempted to activate token {payload} belonging to user {token_doc.get('user_id')}")
                    await message.reply_text(
                        MSG_TOKEN_FAILED.format(reason="This activation link is not for your account.", error_id=uuid.uuid4().hex[:8]),
                        link_preview_options=LinkPreviewOptions(is_disabled=True), quote=True )
                return

            try:
                msg_id = int(payload)
                retrieved_messages = await bot.get_messages(chat_id=Var.BIN_CHANNEL, message_ids=msg_id)
                if not retrieved_messages:
                    await reply_user_err(message, MSG_ERROR_FILE_INVALID) # Use reply_user_err
                    return

                file_msg = retrieved_messages[0] if isinstance(retrieved_messages, list) else retrieved_messages
                if not file_msg:
                    await reply_user_err(message, MSG_ERROR_FILE_INVALID) # Use reply_user_err
                    return

                links_data = await gen_links(file_msg) # Use gen_links, expect dict
                reply_text_content = MSG_LINKS.format(
                    file_name=links_data['media_name'],
                    file_size=links_data['media_size'],
                    download_link=links_data['online_link'],
                    stream_link=links_data['stream_link']
                )
                reply_markup_content = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(MSG_BUTTON_STREAM_NOW, url=links_data['stream_link']),
                        InlineKeyboardButton(MSG_BUTTON_DOWNLOAD, url=links_data['online_link'])
                    ]
                ])
                try:
                    await message.reply_text(
                        text=reply_text_content, reply_markup=reply_markup_content,
                        quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True) )
                except FloodWait as e_fw:
                    logger.warning(f"FloodWait in start_command (deep link): {e_fw}. Sleeping {e_fw.value}s")
                    await asyncio.sleep(e_fw.value)
                    await message.reply_text(
                        text=reply_text_content, reply_markup=reply_markup_content,
                        quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True) )
                return
            except ValueError:
                logger.warning(f"Invalid /start payload from user {user_id_str}: {payload}")
                # Fixed: Added FloodWait handling for this direct reply
                error_message_text = MSG_START_INVALID_PAYLOAD.format(error_id=uuid.uuid4().hex[:8])
                try:
                    await message.reply_text(
                        error_message_text,
                        quote=True,
                        link_preview_options=LinkPreviewOptions(is_disabled=True)
                    )
                except FloodWait as e_fw:
                    logger.warning(f"FloodWait in start_command (invalid payload reply) for {user_id_str}: {e_fw}. Sleeping {e_fw.value}s")
                    await asyncio.sleep(e_fw.value)
                    try:
                        # Re-generate error_id or use a fixed one? For now, re-generate for simplicity.
                        error_message_text_retry = MSG_START_INVALID_PAYLOAD.format(error_id=uuid.uuid4().hex[:8])
                        await message.reply_text(
                            error_message_text_retry,
                            quote=True,
                            link_preview_options=LinkPreviewOptions(is_disabled=True)
                        )
                    except Exception as e_retry:
                        logger.error(f"Failed to send invalid payload error to {user_id_str} on retry: {e_retry}")
                except Exception as e_final:
                    logger.error(f"Failed to send invalid payload error to {user_id_str}: {e_final}")
                return
            except Exception as e:
                logger.error(f"Error processing /start payload '{payload}' for user {user_id_str}: {e}")
                await reply_user_err(message, MSG_FILE_ACCESS_ERROR) # Use reply_user_err
                return

        parts = message.text.strip().split(maxsplit=1)
        if len(message.command) == 1 or (len(parts) > 1 and parts[1].lower() == "start"):
            welcome_text = MSG_WELCOME.format(user_name=message.from_user.first_name if message.from_user else "Guest")
            if Var.FORCE_CHANNEL_ID:
                error_id_context = uuid.uuid4().hex[:8]
                try:
                    chat = await bot.get_chat(Var.FORCE_CHANNEL_ID)
                    invite_link = getattr(chat, 'invite_link', None)
                    chat_username = getattr(chat, 'username', None)
                    chat_title = getattr(chat, 'title', 'Channel')
                    if not invite_link and chat_username: invite_link = f"https://t.me/{chat_username}"
                    if invite_link: welcome_text += f"\n\n{MSG_COMMUNITY_CHANNEL.format(channel_title=chat_title)}"
                    else: logger.warning(f"(ID: {error_id_context}) Could not retrieve invite link for FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID} for /start message text (Channel: {chat_title}, User: {user_id_str}).")
                except Exception as e: logger.error(f"(ID: {error_id_context}) Error adding force channel link to /start message text (User: {user_id_str}, FChannel: {Var.FORCE_CHANNEL_ID}): {e}")

            reply_markup_buttons = [[ InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command"), InlineKeyboardButton(MSG_BUTTON_ABOUT, callback_data="about_command"),],
                [InlineKeyboardButton(MSG_BUTTON_GITHUB, url="https://github.com/fyaz05/FileToLink/"), InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]
            if Var.FORCE_CHANNEL_ID:
                try:
                    chat = await bot.get_chat(Var.FORCE_CHANNEL_ID)
                    invite_link = getattr(chat, 'invite_link', None)
                    chat_username = getattr(chat, 'username', None)
                    if not invite_link and chat_username: invite_link = f"https://t.me/{chat_username}"
                    if invite_link: reply_markup_buttons.append([InlineKeyboardButton(MSG_BUTTON_JOIN_CHANNEL.format(channel_title=getattr(chat, 'title', 'Channel')), url=invite_link)])
                except Exception as e: logger.error(f"Error adding force channel button to /start message (User: {user_id_str}, FChannel: {Var.FORCE_CHANNEL_ID}): {e}")

            reply_markup = InlineKeyboardMarkup(reply_markup_buttons)
            try:
                await message.reply_text(text=welcome_text, reply_markup=reply_markup, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
            except FloodWait as e_fw:
                logger.warning(f"FloodWait in start_command (welcome): {e_fw}. Sleeping {e_fw.value}s")
                await asyncio.sleep(e_fw.value)
                await message.reply_text(text=welcome_text, reply_markup=reply_markup, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
            return
    except Exception as e:
        logger.error(f"Error in start_command for user {user_id_str}: {e}")
        await reply_user_err(message, MSG_ERROR_GENERIC) # Use reply_user_err

@check_banned
@StreamBot.on_message(filters.command("help") & filters.private)
async def help_command(bot: Client, message: Message):
    try:
        user_id_str = str(message.from_user.id) if message.from_user else 'unknown'
        if message.from_user:
            await log_newusr(bot, message.from_user.id, message.from_user.first_name)
        help_text = MSG_HELP
        buttons = [[InlineKeyboardButton(MSG_BUTTON_ABOUT, callback_data="about_command")]]
        if Var.FORCE_CHANNEL_ID:
            error_id_context_help = uuid.uuid4().hex[:8]
            try:
                chat = await bot.get_chat(Var.FORCE_CHANNEL_ID)
                invite_link = getattr(chat, 'invite_link', None)
                chat_username = getattr(chat, 'username', None)
                chat_title = getattr(chat, 'title', 'Channel')
                if not invite_link and chat_username: invite_link = f"https://t.me/{chat_username}"
                if invite_link: buttons.append([InlineKeyboardButton(MSG_BUTTON_JOIN_CHANNEL.format(channel_title=chat_title), url=invite_link)])
                else: logger.warning(f"(ID: {error_id_context_help}) Could not retrieve or construct invite link for FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID} for /help command (User: {user_id_str}).")
            except Exception as e_help: logger.error(f"(ID: {error_id_context_help}) Error adding force channel button to /help command (User: {user_id_str}, FChannel: {Var.FORCE_CHANNEL_ID}): {e_help}")
        buttons.append([InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")])
        reply_markup = InlineKeyboardMarkup(buttons)
        try:
            await message.reply_text(text=help_text, reply_markup=reply_markup, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
        except FloodWait as e_fw: # Added FloodWait handling
            logger.warning(f"FloodWait in help_command: {e_fw}. Sleeping {e_fw.value}s")
            await asyncio.sleep(e_fw.value)
            await message.reply_text(text=help_text, reply_markup=reply_markup, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception as e:
        logger.error(f"Error in help_command: {e}")
        await reply_user_err(message, MSG_ERROR_GENERIC) # Use reply_user_err

@check_banned
@StreamBot.on_message(filters.command("about") & filters.private)
async def about_command(bot: Client, message: Message):
    try:
        if message.from_user:
            await log_newusr(bot, message.from_user.id, message.from_user.first_name)
        about_text = MSG_ABOUT
        buttons = [[InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command")],
                   [InlineKeyboardButton(MSG_BUTTON_GITHUB, url="https://github.com/fyaz05/FileToLink/"), InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]
        reply_markup = InlineKeyboardMarkup(buttons)
        try:
            await message.reply_text(text=about_text, reply_markup=reply_markup, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
        except FloodWait as e_fw: # Added FloodWait handling
            logger.warning(f"FloodWait in about_command: {e_fw}. Sleeping {e_fw.value}s")
            await asyncio.sleep(e_fw.value)
            await message.reply_text(text=about_text, reply_markup=reply_markup, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception as e:
        logger.error(f"Error in about_command: {e}")
        await reply_user_err(message, MSG_ERROR_GENERIC) # Use reply_user_err

@check_banned
@force_channel_check
@StreamBot.on_message(filters.command("dc"))
async def dc_command(bot: Client, message: Message):
    try:
        if not message.from_user and not message.reply_to_message:
            await reply_user_err(message, MSG_DC_ANON_ERROR); return

        async def process_dc_info(usr_obj: User): # Renamed param
            dc_text_val = await gen_dc_txt(usr_obj) # Use gen_dc_txt
            buttons = []
            profile_url = f"https://t.me/{usr_obj.username}" if usr_obj.username else f"tg://user?id={usr_obj.id}"
            buttons.append([InlineKeyboardButton(MSG_BUTTON_VIEW_PROFILE, url=profile_url)])
            buttons.append([InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")])
            dc_keyboard = InlineKeyboardMarkup(buttons)
            try:
                await message.reply_text(dc_text_val, reply_markup=dc_keyboard, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
            except FloodWait as e_fw: # Added FloodWait handling
                logger.warning(f"FloodWait in dc_command (user_info): {e_fw}. Sleeping {e_fw.value}s")
                await asyncio.sleep(e_fw.value)
                await message.reply_text(dc_text_val, reply_markup=dc_keyboard, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True))

        async def process_file_dc_info(file_msg_obj: Message): # Renamed param
            try:
                fname = get_fname(file_msg_obj) or "Untitled File" # Use get_fname
                fsize = humanbytes(get_fsize(file_msg_obj)) # Use get_fsize
                file_type_map = {"document": MSG_FILE_TYPE_DOCUMENT, "photo": MSG_FILE_TYPE_PHOTO, "video": MSG_FILE_TYPE_VIDEO, "audio": MSG_FILE_TYPE_AUDIO, "voice": MSG_FILE_TYPE_VOICE, "sticker": MSG_FILE_TYPE_STICKER, "animation": MSG_FILE_TYPE_ANIMATION, "video_note": MSG_FILE_TYPE_VIDEO_NOTE }
                file_type_attr = next((attr for attr in file_type_map if getattr(file_msg_obj, attr, None)), "unknown")
                file_type_display = file_type_map.get(file_type_attr, MSG_FILE_TYPE_UNKNOWN)
                dc_id = MSG_DC_UNKNOWN
                # Accessing raw attributes for dc_id is fragile; prefer direct attributes if available
                # For simplicity, this part is kept, but usually, dc_id isn't reliably on media.document this way
                if hasattr(file_msg_obj, 'raw') and hasattr(file_msg_obj.raw, 'media') and hasattr(file_msg_obj.raw.media, 'document') and hasattr(file_msg_obj.raw.media.document, 'dc_id'):
                    dc_id = file_msg_obj.raw.media.document.dc_id

                dc_text_val = MSG_DC_FILE_INFO.format(file_name=fname, file_size=fsize, file_type=file_type_display, dc_id=dc_id)
                try:
                    await message.reply_text(dc_text_val, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
                except FloodWait as e_fw: # Added FloodWait handling
                    logger.warning(f"FloodWait in dc_command (file_info): {e_fw}. Sleeping {e_fw.value}s")
                    await asyncio.sleep(e_fw.value)
                    await message.reply_text(dc_text_val, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
            except Exception as e_file_proc:
                logger.error(f"Error processing file info for DC command: {e_file_proc}")
                await reply_user_err(message, MSG_DC_FILE_ERROR)

        args = message.text.strip().split(maxsplit=1)
        if len(args) > 1:
            query = args[1].strip()
            usr_obj = await get_user(bot, query) # Use get_user
            if usr_obj: await process_dc_info(usr_obj)
            else: await reply_user_err(message, MSG_ERROR_USER_INFO)
            return

        if message.reply_to_message:
            if has_media(message.reply_to_message): await process_file_dc_info(message.reply_to_message); return
            elif message.reply_to_message.from_user: await process_dc_info(message.reply_to_message.from_user); return
            else: await reply_user_err(message, MSG_DC_INVALID_USAGE); return

        if message.from_user: await process_dc_info(message.from_user)
        else: await reply_user_err(message, MSG_DC_ANON_ERROR)
    except Exception as e:
        logger.error(f"Error in dc_command: {e}")
        await reply_user_err(message, MSG_ERROR_GENERIC)

@check_banned
@force_channel_check
@StreamBot.on_message(filters.command("ping") & filters.private)
async def ping_command(bot: Client, message: Message):
    try:
        start_ping_time = time.time() # Renamed
        sent_msg = await message.reply_text(MSG_PING_START, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
        end_ping_time = time.time() # Renamed
        time_taken_ms = (end_ping_time - start_ping_time) * 1000
        buttons = [[InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command"), InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]
        await sent_msg.edit_text(MSG_PING_RESPONSE.format(time_taken_ms=time_taken_ms), reply_markup=InlineKeyboardMarkup(buttons), link_preview_options=LinkPreviewOptions(is_disabled=True))
    except FloodWait as e_fw: # Added FloodWait for initial reply
        logger.warning(f"FloodWait in ping_command: {e_fw}. Sleeping {e_fw.value}s")
        await asyncio.sleep(e_fw.value)
        # Retry the whole sequence if initial send failed due to FloodWait
        start_ping_time = time.time()
        sent_msg = await message.reply_text(MSG_PING_START, quote=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
        end_ping_time = time.time()
        time_taken_ms = (end_ping_time - start_ping_time) * 1000
        buttons = [[InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command"), InlineKeyboardButton(MSG_BUTTON_CLOSE, callback_data="close_panel")]]
        await sent_msg.edit_text(MSG_PING_RESPONSE.format(time_taken_ms=time_taken_ms), reply_markup=InlineKeyboardMarkup(buttons), link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception as e:
        logger.error(f"Error in ping_command: {e}")
        await reply_user_err(message, MSG_ERROR_GENERIC)

# handle_callback_error and close_panel_callback are typically in callbacks.py, not common.py
# Removing them from here as they are not general command handlers.
# If they were meant for this file, their imports (uuid for handle_callback_error) would need to be managed.
# For now, assuming they belong in callbacks.py as per standard structure.
