# Thunder/utils/force_channel.py

import asyncio

from pyrogram import Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserNotParticipant

from Thunder.vars import Var
from Thunder.utils.logger import logger
from Thunder.utils.messages import MSG_COMMUNITY_CHANNEL

_force_link = None
_force_title = None

async def get_force_info(bot: Client):
    global _force_link, _force_title
    
    if not Var.FORCE_CHANNEL_ID:
        return None, None
    
    if _force_link is not None and _force_title is not None:
        return _force_link, _force_title
    
    try:
        chat = await bot.get_chat(Var.FORCE_CHANNEL_ID)
        _force_link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else None)
        _force_title = chat.title or "Channel"
        return _force_link, _force_title
    except Exception as e:
        logger.error(f"Force channel error: {e}", exc_info=True)
        return None, None

async def force_channel_check(client: Client, message: Message):
    if not Var.FORCE_CHANNEL_ID:
        return True
    try:
        await client.get_chat_member(Var.FORCE_CHANNEL_ID, message.from_user.id)
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await force_channel_check(client, message)
    except UserNotParticipant:
        link, title = await get_force_info(client)
        if link and title:
            await message.reply_text(
                MSG_COMMUNITY_CHANNEL.format(channel_title=title),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Join", url=link)
                ]])
            )
        else:
            await message.reply_text("You must join the channel to use this bot.")
        return False
    except Exception as e:
        logger.error(f"Error checking force channel: {e}", exc_info=True)
        await message.reply_text("An unexpected error occurred while checking channel membership. Please try again.")
        return False
