from typing import Any, Callable, Coroutine
from pyrogram import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, PeerIdInvalid
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from Thunder.vars import Var
from Thunder.utils.error_handling import log_errors

def force_channel_check(
    func: Callable[[Client, Message], Coroutine[Any, Any, Any]]
) -> Callable[[Client, Message], Coroutine[Any, Any, Any]]:
    
    @log_errors
    async def wrapper(client: Client, message: Message, *args, **kwargs) -> None:
        if not Var.FORCE_CHANNEL_ID or message.chat.id == Var.FORCE_CHANNEL_ID:
            return await func(client, message, *args, **kwargs)
        try:
            member = await client.get_chat_member(Var.FORCE_CHANNEL_ID, message.from_user.id)
            if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                return await func(client, message, *args, **kwargs)
        except UserNotParticipant:
            pass
        except (ChatAdminRequired, PeerIdInvalid):
            return await func(client, message, *args, **kwargs)
        chat = await client.get_chat(Var.FORCE_CHANNEL_ID)
        invite_link = f"https://t.me/{chat.username}" if chat.username else chat.invite_link
        if invite_link:
            await message.reply(
                f"Please join {chat.title} to use this bot.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Join Channel", url=invite_link)
                ]])
            )
    return wrapper
