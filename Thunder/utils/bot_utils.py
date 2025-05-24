# Thunder/utils/bot_utils.py

import asyncio
import time
import random
import hashlib
import uuid
from typing import Optional, Tuple
from urllib.parse import quote_plus, quote

from pyrogram import Client, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, User, CallbackQuery, LinkPreviewOptions
from pyrogram.enums import ChatMemberStatus
from pyrogram.raw.functions.messages import DeleteHistory
from pyrogram.errors import FloodWait, UserNotParticipant, PeerIdInvalid, ChannelPrivate, ChatAdminRequired, BadRequest

from Thunder.bot import StreamBot
from Thunder.vars import Var
from Thunder.utils.database import db
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_BUTTON_GET_HELP,
    MSG_NEW_USER,
    MSG_DC_UNKNOWN,
    MSG_DC_USER_INFO,
    MSG_LINKS,
    MSG_BUTTON_STREAM_NOW,
    MSG_BUTTON_DOWNLOAD,
    MSG_ERROR_GENERIC_CALLBACK
)
from Thunder.utils.file_properties import get_name, get_media_file_size, get_hash
from Thunder.utils.shortener import shorten


async def notify_channel(bot: Client, text: str):
    try:
        if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
            await bot.send_message(chat_id=Var.BIN_CHANNEL, text=text)
    except Exception:
        pass

async def notify_owner(client: Client, text: str):
    try:
        owner_ids = Var.OWNER_ID
        if isinstance(owner_ids, (list, tuple, set)):
            tasks = [
                client.send_message(chat_id=owner_id, text=text)
                for owner_id in owner_ids
            ]
            results = await asyncio.gather(*tasks)
            await asyncio.sleep(0.1)
        else:
            await client.send_message(chat_id=owner_ids, text=text)
            await asyncio.sleep(0.1)
        
        if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0:
            await client.send_message(chat_id=Var.BIN_CHANNEL, text=text)
            await asyncio.sleep(0.1)
    except Exception as e:
        logger.error(f"Failed to send message to owner: {e}")

async def handle_user_error(message: Message, error_msg: str):
    try:
        await message.reply_text(
            error_msg,
            quote=True,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(MSG_BUTTON_GET_HELP, callback_data="help_command")]
            ])
        )
    except Exception:
        pass

async def log_new_user(bot: Client, user_id: int, first_name: str):
    try:
        if not await db.is_user_exist(user_id): 
            await db.add_user(user_id)
            if hasattr(Var, 'BIN_CHANNEL') and isinstance(Var.BIN_CHANNEL, int) and Var.BIN_CHANNEL != 0: 
                await bot.send_message(
                    Var.BIN_CHANNEL,
                    MSG_NEW_USER.format(
                        first_name=first_name,
                        user_id=user_id
                    )
                )
    except Exception:
        pass

async def generate_media_links(log_msg: Message, shortener: bool = True) -> Tuple[str, str, str, str]:
    try:
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
        
        stream_link_raw = f"{base_url}/watch/{hash_value}{file_id}/{file_name_encoded}"
        online_link_raw = f"{base_url}/{hash_value}{file_id}/{file_name_encoded}"
        
        stream_link = stream_link_raw
        online_link = online_link_raw
        
        if shortener and getattr(Var, "SHORTEN_MEDIA_LINKS", False):
            stream_link = await shorten(stream_link_raw)
            online_link = await shorten(online_link_raw)
        
        return stream_link, online_link, media_name, media_size
    except Exception as e:
        logger.error(f"Error generating media links: {e}")
        return "", "", "", ""

async def send_links_to_user(client: Client, command_message: Message, media_name: str, media_size: str, stream_link: str, online_link: str):
    msg_text = MSG_LINKS.format(
        file_name=media_name,
        file_size=media_size,
        download_link=online_link,
        stream_link=stream_link
    )
    
    try:
        await command_message.reply_text(
            msg_text,
            quote=True,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            parse_mode=enums.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(MSG_BUTTON_STREAM_NOW, url=stream_link),
                    InlineKeyboardButton(MSG_BUTTON_DOWNLOAD, url=online_link)
                ]
            ]),
        )
        await asyncio.sleep(0.3)
    except Exception as e:
        logger.error(f"Error sending links to user: {e}")
        raise

async def generate_dc_text(user: User) -> str:
    dc_id = user.dc_id if user.dc_id is not None else MSG_DC_UNKNOWN
    return MSG_DC_USER_INFO.format(
        user_name=user.first_name or 'User',
        user_id=user.id,
        dc_id=dc_id
    )

async def get_user_safely(bot: Client, query) -> Optional[User]:
    try:
        if isinstance(query, str):
            if query.startswith('@'):
                return await bot.get_users(query)
            elif query.isdigit():
                return await bot.get_users(int(query))
        elif isinstance(query, int):
            return await bot.get_users(query)
        return None
    except Exception:
        return None

async def check_admin_privileges(client: Client, chat_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, client.me.id)
        return member.status in [
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER
        ]
    except Exception:
        return False

async def _execute_command_from_callback(client: Client, callback_query: CallbackQuery, command_name: str, command_func):
    try:
        await callback_query.answer()
        mock_message = callback_query.message
        mock_message.from_user = callback_query.from_user
        mock_message.chat = callback_query.message.chat
        mock_message.text = f"/{command_name}"
        mock_message.command = [command_name]
        await command_func(client, mock_message)
        logger.info(f"User {callback_query.from_user.id} used {command_name} via callback")
    except Exception as e:
        logger.error(f"Error in {command_name}_callback: {e}")
        await callback_query.answer(MSG_ERROR_GENERIC_CALLBACK.format(error_id=uuid.uuid4().hex[:8]), show_alert=True)
