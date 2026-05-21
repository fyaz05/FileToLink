import pytdbot
from pytdbot import types

from Thunder.utils.compat import ChatMemberStatus, get_member_status
from Thunder.utils.logger import logger
from Thunder.utils.messages import MSG_COMMUNITY_CHANNEL
from Thunder.vars import Var

_force_link = None
_force_title = None


async def get_force_info(bot: pytdbot.Client):
    global _force_link, _force_title

    if not Var.FORCE_CHANNEL_ID:
        return None, None

    if _force_link is not None and _force_title is not None:
        return _force_link, _force_title

    try:
        chat = await bot.getChat(chat_id=Var.FORCE_CHANNEL_ID)
        if isinstance(chat, types.Error):
            logger.error(f"Force channel error: {chat.message}")
            return None, None
        if chat:
            invite_link = None
            if hasattr(chat, "invite_link") and chat.invite_link:
                invite_link = chat.invite_link
            if not invite_link:
                username = None
                if hasattr(chat, "type") and isinstance(chat.type, types.ChatTypeSupergroup):
                    username = getattr(chat.type, "supergroup_id", None)
                invite_link = f"https://t.me/c/{username}" if username else None
            _force_link = invite_link
            _force_title = chat.title or "Channel"
        return _force_link, _force_title
    except Exception as e:
        logger.error(f"Force channel error: {e}", exc_info=True)
        return None, None


async def force_channel_check(client: pytdbot.Client, message: types.Message):
    if not Var.FORCE_CHANNEL_ID:
        return True

    from_id = getattr(message, "from_id", None)
    if from_id is None:
        return True

    try:
        member = await client.getChatMember(
            chat_id=Var.FORCE_CHANNEL_ID,
            member_id=types.MessageSenderUser(user_id=from_id)
        )
        if isinstance(member, types.Error):
            if member.code != 404:
                logger.error(f"Error checking force channel: {member.message}")
                return False

        if isinstance(member, types.Error):
            link, title = await get_force_info(client)
            if link and title:
                button = types.InlineKeyboardButton(
                    text="Join",
                    type=types.InlineKeyboardButtonTypeUrl(url=link)
                )
                try:
                    await message.reply_text(
                        MSG_COMMUNITY_CHANNEL.format(channel_title=title),
                        reply_markup=types.ReplyMarkupInlineKeyboard(rows=[[button]])
                    )
                except Exception:
                    logger.debug(f"Failed to send force channel join prompt to user {from_id}")
            else:
                try:
                    await message.reply_text("You must join the channel to use this bot.")
                except Exception:
                    logger.debug(f"Failed to send plain join channel message to user {from_id}")
            return False

        status = get_member_status(member)
        if status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
            link, title = await get_force_info(client)
            if link and title:
                button = types.InlineKeyboardButton(
                    text="Join",
                    type=types.InlineKeyboardButtonTypeUrl(url=link)
                )
                try:
                    await message.reply_text(
                        MSG_COMMUNITY_CHANNEL.format(channel_title=title),
                        reply_markup=types.ReplyMarkupInlineKeyboard(rows=[[button]])
                    )
                except Exception:
                    logger.debug(f"Failed to send force channel join prompt to user {from_id} (left/banned)")
            else:
                try:
                    await message.reply_text("You must join the channel to use this bot.")
                except Exception:
                    logger.debug(f"Failed to send plain join channel message to user {from_id} (left/banned)")
            return False

        return True
    except Exception as e:
        logger.error(f"Error checking force channel: {e}", exc_info=True)
        try:
            await message.reply_text("An unexpected error occurred while checking channel membership.")
        except Exception:
            logger.debug(f"Failed to send unexpected error message to user {from_id}")
        return False
