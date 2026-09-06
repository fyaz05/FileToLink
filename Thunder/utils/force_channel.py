# Thunder/utils/force_channel.py

from pyrogram import Client
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from Thunder.utils.logger import logger
from Thunder.utils.messages import MSG_COMMUNITY_CHANNEL
from Thunder.utils.safe_call import reply_safe, tg_call
from Thunder.vars import Var

_force_link = None
_force_title = None


async def get_force_info(bot: Client):
    global _force_link, _force_title

    if not Var.FORCE_CHANNEL_ID:
        return None, None

    if _force_link is not None and _force_title is not None:
        return _force_link, _force_title

    try:
        chat = await tg_call(bot.get_chat, Var.FORCE_CHANNEL_ID, retries=1)
        if chat:
            # Var.FORCE_CHANNEL_ID is a numeric channel id: get_chat always
            # resolves a full Chat there (ChatPreview only comes from link
            # resolution), so the stub-union members are unreachable.
            _force_link = chat.invite_link or (  # type: ignore[union-attr]
                f"https://t.me/{chat.username}" if chat.username else None  # type: ignore[union-attr]
            )
            _force_title = chat.title or "Channel"
        return _force_link, _force_title
    except Exception as e:
        logger.error(f"Force channel error: {e}", exc_info=True)
        return None, None


async def force_channel_check(client: Client, message: Message):
    if not Var.FORCE_CHANNEL_ID:
        return True

    if message.from_user is None:
        return True

    try:
        member = await tg_call(
            client.get_chat_member,
            Var.FORCE_CHANNEL_ID,
            message.from_user.id,
            retries=1,
        )
        if member is None:
            logger.error(
                f"Failed to get chat member for {message.from_user.id} in "
                f"force channel {Var.FORCE_CHANNEL_ID} after retries."
            )
            return False
        return True
    except UserNotParticipant:
        link, title = await get_force_info(client)
        if link and title:
            try:
                await reply_safe(
                    message,
                    MSG_COMMUNITY_CHANNEL.format(channel_title=title),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Join", url=link)]]),
                )
            except Exception as e:
                logger.warning(f"Could not send force-sub prompt: {e}")
        else:
            try:
                await reply_safe(message, "You must join the channel to use this bot.")
            except Exception as e:
                logger.warning(f"Could not send force-sub notice: {e}")
        return False
    except Exception as e:
        logger.error(f"Error checking force channel: {e}", exc_info=True)
        try:
            await reply_safe(
                message,
                "An unexpected error occurred while checking channel membership. Please try again.",
            )
        except Exception as inner_e:
            logger.warning(f"Could not send force-sub error notice: {inner_e}")
        return False
