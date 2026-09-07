# Thunder/utils/force_channel.py

import html
import time

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_COMMUNITY_CHANNEL,
    MSG_FORCE_JOIN_BUTTON,
    MSG_FORCE_SUB_CHECK_FAILED,
    MSG_FORCE_SUB_REQUIRED,
)
from Thunder.utils.safe_call import reply_safe, tg_call
from Thunder.vars import Var

_force_link = None
_force_title = None
_force_resolved = False
_negative_until = 0.0
_NEGATIVE_TTL_SECONDS = 60.0


async def get_force_info(bot: Client):
    global _force_link, _force_title, _force_resolved, _negative_until

    if not Var.FORCE_CHANNEL_ID:
        return None, None

    # resolved-once: a numeric channel's invite link/title does not change
    # between messages
    if _force_resolved:
        return _force_link, _force_title
    if time.monotonic() < _negative_until:
        return None, None

    try:
        chat = await tg_call(bot.get_chat, Var.FORCE_CHANNEL_ID, retries=1)
        if chat:
            # numeric channel id: get_chat always resolves a full Chat
            # (ChatPreview only comes from link resolution), hence the ignores
            _force_link = chat.invite_link or (  # type: ignore[union-attr]
                f"https://t.me/{chat.username}" if chat.username else None  # type: ignore[union-attr]
            )
            _force_title = chat.title or "Channel"
        # cache even the no-link outcome, or it re-resolves per message
        _force_resolved = True
        return _force_link, _force_title
    except Exception as e:
        # transient RPC failure: short negative cache so the gate path does
        # not hammer get_chat on every message during a Telegram brownout
        _negative_until = time.monotonic() + _NEGATIVE_TTL_SECONDS
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
                    MSG_COMMUNITY_CHANNEL.format(
                        # escaped twin of the /help panel line (common.py);
                        # HTML parse mode skips the markdown pre-pass
                        channel_title=html.escape(title or "Channel")
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton(MSG_FORCE_JOIN_BUTTON, url=link)]]
                    ),
                )
            except Exception as e:
                logger.warning(f"Could not send force-sub prompt: {e}")
        else:
            try:
                await reply_safe(message, MSG_FORCE_SUB_REQUIRED)
            except Exception as e:
                logger.warning(f"Could not send force-sub notice: {e}")
        return False
    except Exception as e:
        logger.error(f"Error checking force channel: {e}", exc_info=True)
        try:
            await reply_safe(message, MSG_FORCE_SUB_CHECK_FAILED)
        except Exception as inner_e:
            logger.warning(f"Could not send force-sub error notice: {inner_e}")
        return False
