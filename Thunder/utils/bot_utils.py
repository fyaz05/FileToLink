# Thunder/utils/bot_utils.py

import asyncio
from typing import Optional, Tuple
from urllib.parse import quote

from pyrogram import Client, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, User, CallbackQuery, LinkPreviewOptions, ReplyParameters
from pyrogram.enums import ChatMemberStatus

from Thunder.vars import Var
from Thunder.utils.database import db
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.retry_api import retry_send_message
from Thunder.utils.logger import logger
from Thunder.utils.error_handling import log_errors
from Thunder.utils.messages import (
    MSG_BUTTON_GET_HELP,
    MSG_NEW_USER,
    MSG_DC_UNKNOWN,
    MSG_DC_USER_INFO,
    MSG_LINKS,
    MSG_BUTTON_STREAM_NOW,
    MSG_BUTTON_DOWNLOAD
)
from Thunder.utils.file_properties import get_name, get_media_file_size, get_hash
from Thunder.utils.shortener import shorten

@log_errors
async def notify_channel(bot: Client, text: str):
    if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
        await bot.send_message(chat_id=Var.BIN_CHANNEL, text=text)

@log_errors
async def notify_owner(client: Client, text: str):
    owner_ids = Var.OWNER_ID
    tasks = [retry_send_message(client, oid, text) for oid in (owner_ids if isinstance(owner_ids, (list, tuple, set)) else [owner_ids])]
    if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
        tasks.append(retry_send_message(client, Var.BIN_CHANNEL, text))
    await asyncio.gather(*tasks, return_exceptions=True)

@log_errors
async def handle_user_error(message: Message, error_msg: str):
    await retry_send_message(
        message._client,
        message.chat.id,
        error_msg,
        reply_parameters=ReplyParameters(message_id=message.id),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command")]
        ])
    )

@log_errors
async def log_new_user(bot: Client, user_id: int, first_name: str):
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id)
        if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
            await retry_send_message(
                bot,
                Var.BIN_CHANNEL,
                MSG_NEW_USER.format(first_name=first_name, user_id=user_id)
            )

@log_errors
async def generate_media_links(log_msg: Message, shortener: bool = True) -> Tuple[str, str, str, str]:
    base_url = Var.URL.rstrip("/")
    file_id = log_msg.id
    media_name = get_name(log_msg)
    if isinstance(media_name, bytes):
        media_name = media_name.decode('utf-8', errors='replace')
    else:
        media_name = str(media_name)
    media_size = humanbytes(get_media_file_size(log_msg))
    file_name_encoded = quote(media_name)
    hash_value = get_hash(log_msg)
    stream_link = f"{base_url}/watch/{hash_value}{file_id}/{file_name_encoded}"
    online_link = f"{base_url}/{hash_value}{file_id}/{file_name_encoded}"
    if shortener and getattr(Var, "SHORTEN_MEDIA_LINKS", False):
        shortened_results = await asyncio.gather(
            shorten(stream_link),
            shorten(online_link),
            return_exceptions=True
        )
        if not isinstance(shortened_results[0], Exception):
            stream_link = shortened_results[0]
        if not isinstance(shortened_results[1], Exception):
            online_link = shortened_results[1]
    return stream_link, online_link, media_name, media_size

@log_errors
async def send_links_to_user(client: Client, command_message: Message, media_name: str, media_size: str, stream_link: str, online_link: str):
    msg_text = MSG_LINKS.format(
        file_name=media_name,
        file_size=media_size,
        download_link=online_link,
        stream_link=stream_link
    )
    await retry_send_message(
        client,
        command_message.chat.id,
        msg_text,
        reply_parameters=ReplyParameters(message_id=command_message.id),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        parse_mode=enums.ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(MSG_BUTTON_STREAM_NOW, url=stream_link),
                InlineKeyboardButton(MSG_BUTTON_DOWNLOAD, url=online_link)
            ]
        ])
    )

@log_errors
async def generate_dc_text(user: User) -> str:
    dc_id = user.dc_id if user.dc_id is not None else MSG_DC_UNKNOWN
    return MSG_DC_USER_INFO.format(
        user_name=user.first_name or 'User',
        user_id=user.id,
        dc_id=dc_id
    )

@log_errors
async def get_user_safely(bot: Client, query) -> Optional[User]:
    if isinstance(query, str):
        if query.startswith('@'):
            return await bot.get_users(query)
        elif query.isdigit():
            return await bot.get_users(int(query))
    elif isinstance(query, int):
        return await bot.get_users(query)
    return None

@log_errors
async def check_admin_privileges(client: Client, chat_id: int) -> bool:
    member = await client.get_chat_member(chat_id, client.me.id)
    return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]

@log_errors
async def _execute_command_from_callback(client: Client, callback_query: CallbackQuery, command_name: str, command_func):
    await callback_query.answer()
    mock_message = callback_query.message
    mock_message.from_user = callback_query.from_user
    mock_message.chat = callback_query.message.chat
    mock_message.text = f"/{command_name}"
    mock_message.command = [command_name]
    await command_func(client, mock_message)
