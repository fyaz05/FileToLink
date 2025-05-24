"""
Decorator to enforce channel membership before allowing command execution in Pyrogram bots.
"""

from typing import Any, Callable, Coroutine
import asyncio
import uuid

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, RightForbidden, RPCError, FloodWait
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from Thunder.vars import Var
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_FORCE_CHANNEL_ERROR,
    MSG_FORCE_CHANNEL_RPC_ERROR,
    MSG_FORCE_CHANNEL_GENERIC_ERROR,
    MSG_FORCE_CHANNEL_NO_LINK,
    MSG_FORCE_CHANNEL_MEMBERSHIP_REQUIRED,
    MSG_FORCE_CHANNEL_SERVICE_INTERRUPTION,
    MSG_FORCE_CHANNEL_ACCESS_REQUIRED
)

def force_channel_check(
    func: Callable[[Client, Message], Coroutine[Any, Any, Any]]
) -> Callable[[Client, Message], Coroutine[Any, Any, Any]]:
    """Decorator to enforce channel membership before allowing command execution."""
    async def wrapper(client: Client, message: Message, *args, **kwargs) -> None:
        if not Var.FORCE_CHANNEL_ID:
            await func(client, message, *args, **kwargs)
            return

        if message.chat.id == Var.FORCE_CHANNEL_ID:
            await func(client, message, *args, **kwargs)
            return

        user_id = message.from_user.id
        try:
            member = await client.get_chat_member(
                chat_id=Var.FORCE_CHANNEL_ID,
                user_id=user_id
            )
            if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                await func(client, message, *args, **kwargs)
                return
        except UserNotParticipant:
            logger.debug(f"[ForceChannelCheck] User {user_id} is not a participant of FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID}. Prompting to join.")
        except (ChatAdminRequired, RightForbidden):
            logger.error(f"[ForceChannelCheck] Bot lacks permissions in FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID}.")
            await message.reply(MSG_FORCE_CHANNEL_ERROR.format(error_id=uuid.uuid4().hex[:8]))
            return
        except asyncio.TimeoutError:
            error_id = uuid.uuid4().hex[:8]
            logger.error(f"[ForceChannelCheck] TimeoutError (ID: {error_id}) for user {user_id} while checking membership in {Var.FORCE_CHANNEL_ID}.")
            await message.reply(MSG_FORCE_CHANNEL_RPC_ERROR.format(error_id=error_id))
            return
        except RPCError as e:
            logger.error(f"[ForceChannelCheck] RPCError for user {user_id} in {Var.FORCE_CHANNEL_ID}: {e}")
            await message.reply(MSG_FORCE_CHANNEL_RPC_ERROR.format(error_id=uuid.uuid4().hex[:8]))
            return
        except FloodWait as e:
            wait_time: int = e.value
            logger.debug(f"[ForceChannelCheck] Flood wait for {wait_time} seconds")
            await asyncio.sleep(wait_time)
            return await wrapper(client, message, *args, **kwargs)
        except Exception as e:
            logger.error(f"[ForceChannelCheck] Unexpected error for user {user_id} in {Var.FORCE_CHANNEL_ID}: {e}")
            await message.reply(MSG_FORCE_CHANNEL_GENERIC_ERROR.format(error_id=uuid.uuid4().hex[:8]))
            return

        try:
            chat = await client.get_chat(Var.FORCE_CHANNEL_ID)
            invite_link = f"https://t.me/{chat.username}" if chat.username else chat.invite_link
            if not invite_link:
                logger.warning(f"[ForceChannelCheck] No invite link for {Var.FORCE_CHANNEL_ID} ({chat.title})")
                await message.reply(MSG_FORCE_CHANNEL_NO_LINK)
                return

            await message.reply(
                MSG_FORCE_CHANNEL_MEMBERSHIP_REQUIRED.format(channel_name=chat.title, error_id=uuid.uuid4().hex[:8]),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(MSG_FORCE_CHANNEL_ACCESS_REQUIRED, url=invite_link)],
                ]),
            )
        except asyncio.TimeoutError:
            error_id = uuid.uuid4().hex[:8]
            logger.error(f"[ForceChannelCheck] TimeoutError (ID: {error_id}) getting chat/invite link for {Var.FORCE_CHANNEL_ID}.")
            await message.reply(MSG_FORCE_CHANNEL_SERVICE_INTERRUPTION.format(error_id=error_id))
        except RPCError as e:
            logger.error(f"[ForceChannelCheck] RPCError getting chat/invite link for {Var.FORCE_CHANNEL_ID}: {e}")
            await message.reply(MSG_FORCE_CHANNEL_SERVICE_INTERRUPTION.format(error_id=uuid.uuid4().hex[:8]))
        except Exception as e:
            logger.error(f"[ForceChannelCheck] Unexpected error getting chat/invite link for {Var.FORCE_CHANNEL_ID}: {e}")
            await message.reply(MSG_FORCE_CHANNEL_GENERIC_ERROR.format(error_id=uuid.uuid4().hex[:8]))
    return wrapper


