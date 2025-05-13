"""
Decorator to enforce channel membership before allowing command execution in Pyrogram bots.
"""

from typing import Any, Callable, Coroutine

from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, RightForbidden, RPCError
from pyrogram.types import Message, LinkPreviewOptions

from Thunder.vars import Var
from Thunder.utils.logger import logger


def force_channel_check(
    func: Callable[[Any, Message], Coroutine[Any, Any, Any]]
) -> Callable[[Any, Message], Coroutine[Any, Any, Any]]:
    """Decorator to enforce channel membership before allowing command execution."""
    async def wrapper(client: Any, message: Message) -> None:
        # Bypass check if force channel not configured
        if not Var.FORCE_CHANNEL_ID:
            return await func(client, message)

        # Allow messages from the force channel itself
        if message.chat.id == Var.FORCE_CHANNEL_ID:
            return await func(client, message)

        user_id = message.from_user.id
        try:
            member = await client.get_chat_member(
                chat_id=Var.FORCE_CHANNEL_ID,
                user_id=user_id
            )
            if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                return await func(client, message)
            else:
                logger.info(f"[ForceChannelCheck] User {user_id} has status {member.status}, not allowed.")
        except UserNotParticipant:
            logger.info(f"[ForceChannelCheck] User {user_id} is not a participant of FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID}. Prompting to join.")
        except (ChatAdminRequired, RightForbidden):
            logger.error(f"[ForceChannelCheck] Bot lacks permissions (ChatAdminRequired or RightForbidden) in FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID} to check membership for user {user_id}.")
            await message.reply("Sorry, there was an issue verifying access. Please try again later.")
            return
        except RPCError as e:
            logger.error(f"[ForceChannelCheck] RPCError checking membership for user {user_id} in FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID}: {e}")
            await message.reply("An unexpected error occurred while checking channel membership. Please try again.")
            return
        except Exception as e:
            logger.error(f"[ForceChannelCheck] Unexpected error checking membership for user {user_id} in FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID}: {e}")
            await message.reply("An error occurred. Please try again.")
            return

        try:
            chat = await client.get_chat(Var.FORCE_CHANNEL_ID)
            # Prefer username-based link if available
            invite_link = f"https://t.me/{chat.username}" if chat.username else chat.invite_link

            if not invite_link:
                logger.warning(f"[ForceChannelCheck] No invite link available for FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID} ({chat.title})")
                await message.reply(
                    "To use this bot, you must join our main channel. Please contact an admin for assistance.",
                    link_preview_options=LinkPreviewOptions(is_disabled=True)
                )
                return

            response_msg = (
                "🚫 **Access Required**\n\n"
                f"Please join our channel to use this bot:\n{invite_link}\n\n"
                "After joining, try your command again."
            )

            await message.reply(
                response_msg,
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
        except RPCError as e:
            logger.error(f"[ForceChannelCheck] RPCError in force channel check: {e}")
            await message.reply(
                "⚠️ Temporary service interruption. Please try again later.",
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
        except Exception as e:
            logger.error(f"[ForceChannelCheck] Unexpected error in force channel check: {e}")
            await message.reply(
                "🔒 This command requires channel membership. Please contact support if you need assistance.",
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
    return wrapper
