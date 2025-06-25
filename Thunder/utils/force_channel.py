# Thunder/utils/force_channel.py

from pyrogram import Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from Thunder.vars import Var
from Thunder.utils.logger import logger
from Thunder.utils.messages import MSG_COMMUNITY_CHANNEL

async def get_force_info(bot: Client):
    if not Var.FORCE_CHANNEL_ID:
        return None, None
    try:
        chat = await bot.get_chat(Var.FORCE_CHANNEL_ID)
        link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else None)
        return link, chat.title or "Channel"
    except Exception as e:
        logger.error(f"Force channel error: {e}")
        return None, None

async def force_channel_check(client: Client, message: Message):
    if not Var.FORCE_CHANNEL_ID:
        return True
    
    try:
        await client.get_chat_member(Var.FORCE_CHANNEL_ID, message.from_user.id)
        return True
    except:
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
