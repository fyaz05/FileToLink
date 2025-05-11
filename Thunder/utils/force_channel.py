"""
Decorator to enforce channel membership before allowing command execution in Pyrogram bots.
"""

from typing import Any, Callable, Coroutine

from pyrogram.errors import UserNotParticipant, ChatAdminRequired, RightForbidden, RPCError
from pyrogram.types import Message

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
            if member.status in ["member", "administrator", "creator"]:
                return await func(client, message)
        except UserNotParticipant:
            logger.info(f"User {user_id} is not a participant of FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID}. Prompting to join.")
        except (ChatAdminRequired, RightForbidden):
            logger.error(f"Bot lacks permissions (ChatAdminRequired or RightForbidden) in FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID} to check membership for user {user_id}.")
            await message.reply("Sorry, there was an issue verifying access. Please try again later.")
            return
        except RPCError as e:
            logger.error(f"RPCError checking membership for user {user_id} in FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID}: {e}")
            await message.reply("An unexpected error occurred while checking channel membership. Please try again.")
            return
        except Exception as e:
            logger.error(f"Unexpected error checking membership for user {user_id} in FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID}: {e}")
            await message.reply("An error occurred. Please try again.")
            return

        try:
            chat = await client.get_chat(Var.FORCE_CHANNEL_ID)
            # Prefer username-based link if available
            invite_link = f"https://t.me/{chat.username}" if chat.username else chat.invite_link

            if not invite_link:
                logger.warning(f"No invite link available for FORCE_CHANNEL_ID {Var.FORCE_CHANNEL_ID} ({chat.title})")
                await message.reply(
                    "To use this bot, you must join our main channel. Please contact an admin for assistance.",
                    disable_web_page_preview=True
                )
                return

            response_msg = (
                "🚫 **Access Required**\n\n"
                f"Please join our channel to use this bot:\n{invite_link}\n\n"
                "_After joining, try your command again._"
            )

            await message.reply(
                response_msg,
                disable_web_page_preview=True
            )
        except RPCError as e:
            logger.error(f"RPCError in force channel check: {e}")
            await message.reply(
                "⚠️ Temporary service interruption. Please try again later.",
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Unexpected error in force channel check: {e}")
            await message.reply(
                "🔒 This command requires channel membership. Please contact support if you need assistance.",
                disable_web_page_preview=True
            )
    return wrapper