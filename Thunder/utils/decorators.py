from pytdbot import types

from Thunder.utils.database import db
from Thunder.utils.logger import logger
from Thunder.utils.messages import MSG_DECORATOR_BANNED, MSG_ERROR_UNAUTHORIZED, MSG_TOKEN_INVALID
from Thunder.utils.shortener import shorten
from Thunder.utils.tokens import allowed, check, generate
from Thunder.vars import Var


async def check_banned(client, message: types.Message):
    try:
        from_id = getattr(message, "from_id", None)
        if not from_id:
            return True
        if from_id == Var.OWNER_ID:
            return True

        ban_details = await db.is_user_banned(from_id)
        if ban_details:
            banned_at = ban_details.get('banned_at')
            ban_time = (
                banned_at.strftime('%B %d, %Y, %I:%M %p UTC')
                if banned_at and hasattr(banned_at, 'strftime')
                else str(banned_at) if banned_at else 'N/A'
            )
            try:
                await message.reply_text(
                    MSG_DECORATOR_BANNED.format(
                        reason=ban_details.get('reason', 'Not specified'),
                        ban_time=ban_time
                    )
                )
            except Exception as e:
                logger.error(f"Error sending ban message: {e}")
            logger.debug(f"Blocked banned user {from_id}.")
            return False
        return True
    except Exception as e:
        logger.error(f"Error in check_banned: {e}", exc_info=True)
        return True


async def require_token(client, message: types.Message):
    try:
        from_id = getattr(message, "from_id", None)
        if not from_id:
            return True

        if not getattr(Var, "TOKEN_ENABLED", False):
            return True

        if from_id == Var.OWNER_ID or await allowed(from_id) or await check(from_id):
            return True

        temp_token_string = None
        try:
            temp_token_string = await generate(from_id)
        except Exception as e:
            logger.error(f"Failed to generate token for user {from_id}: {e}", exc_info=True)
            try:
                await message.reply_text("Sorry, could not generate an access token link. Please try again later.")
            except Exception:
                logger.debug(f"Failed to send token generation error to user {from_id}")
            return False

        if not temp_token_string:
            logger.error(f"Token generation returned empty for user {from_id}")
            try:
                await message.reply_text("Sorry, could not generate an access token link. Please try again later.")
            except Exception:
                logger.debug(f"Failed to send token empty error to user {from_id}")
            return False

        me = await client.getMe()
        if isinstance(me, types.Error):
            logger.error(f"Failed to get bot info: {me.message}")
            return False

        bot_username = None
        if hasattr(me, "usernames") and me.usernames:
            bot_username = me.usernames.editable_username or (me.usernames.active_usernames[0] if me.usernames.active_usernames else None)
        if not bot_username:
            bot_username = getattr(me, "username", None)

        deep_link = f"https://t.me/{bot_username}?start={temp_token_string}"
        short_url = deep_link

        try:
            short_url_result = await shorten(deep_link)
            if short_url_result:
                short_url = short_url_result
        except Exception as e:
            logger.warning(f"Failed to shorten token link: {e}")

        button = types.InlineKeyboardButton(
            text="Activate Access",
            type=types.InlineKeyboardButtonTypeUrl(url=short_url)
        )
        try:
            await message.reply_text(
                MSG_TOKEN_INVALID,
                reply_markup=types.ReplyMarkupInlineKeyboard(rows=[[button]])
            )
        except Exception as e:
            logger.error(f"Error sending token message: {e}")
        logger.debug(f"Sent token activation link to user {from_id}.")
        return False
    except Exception as e:
        logger.error(f"Error in require_token: {e}", exc_info=True)
        return False


async def get_shortener_status(client, message: types.Message):
    try:
        from_id = getattr(message, "from_id", None)
        use_shortener = getattr(Var, "SHORTEN_MEDIA_LINKS", False)
        if from_id:
            try:
                if from_id == Var.OWNER_ID or await allowed(from_id):
                    use_shortener = False
            except Exception as e:
                logger.warning(f"Error checking allowed status: {e}")
        return use_shortener
    except Exception as e:
        logger.error(f"Error in get_shortener_status: {e}", exc_info=True)
        return getattr(Var, "SHORTEN_MEDIA_LINKS", False)


async def owner_only(client, update):
    try:
        from_id = None
        if hasattr(update, "from_id"):
            from_id = update.from_id
        elif hasattr(update, "sender_id"):
            sender = update.sender_id
            if isinstance(sender, types.MessageSenderUser):
                from_id = sender.user_id

        if not from_id or from_id != Var.OWNER_ID:
            if hasattr(update, "answer"):
                await update.answer(MSG_ERROR_UNAUTHORIZED, show_alert=True)
            logger.warning(f"Unauthorized access attempt by {from_id}")
            return False
        return True
    except Exception as e:
        logger.error(f"Error in owner_only: {e}", exc_info=True)
        return False
