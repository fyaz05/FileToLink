# Thunder/utils/decorators.py

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from Thunder.utils.database import db
from Thunder.utils.handler import handle_flood_wait
from Thunder.utils.logger import logger
from Thunder.utils.messages import (MSG_DECORATOR_BANNED,
                                    MSG_ERROR_UNAUTHORIZED, MSG_TOKEN_INVALID)
from Thunder.utils.shortener import shorten
from Thunder.utils.tokens import allowed, check, generate
from Thunder.vars import Var


async def check_banned(client, message: Message):
    try:
        if not message.from_user:
            return True
        user_id = message.from_user.id
        if user_id == Var.OWNER_ID:
            return True

        ban_details = await db.is_user_banned(user_id)
        if ban_details:
            banned_at = ban_details.get('banned_at')
            ban_time = (
                banned_at.strftime('%B %d, %Y, %I:%M %p UTC')
                if banned_at and hasattr(banned_at, 'strftime')
                else str(banned_at) if banned_at else 'N/A'
            )
            await handle_flood_wait(
                message.reply_text,
                MSG_DECORATOR_BANNED.format(
                    reason=ban_details.get('reason', 'Not specified'),
                    ban_time=ban_time
                ),
                quote=True
            )
            logger.debug(f"Blocked banned user {user_id}.")
            return False
        return True
    except Exception as e:
        logger.error(f"Error in check_banned: {e}", exc_info=True)
        return True

async def require_token(client, message: Message):
    try:
        if not message.from_user:
            return True

        if not getattr(Var, "TOKEN_ENABLED", False):
            return True

        user_id = message.from_user.id
        if user_id == Var.OWNER_ID or await allowed(user_id) or await check(user_id):
            return True

        temp_token_string = None
        try:
            temp_token_string = await generate(user_id)
        except Exception as e:
            logger.error(f"Failed to generate temporary token for user {user_id} in require_token: {e}", exc_info=True)
            await handle_flood_wait(message.reply_text, "Sorry, could not generate an access token link. Please try again later.", quote=True)
            return False

        if not temp_token_string:
            logger.error(f"Temporary token generation returned empty for user {user_id} in require_token.", exc_info=True)
            await handle_flood_wait(message.reply_text, "Sorry, could not generate an access token link. Please try again later.", quote=True)
            return False

        me = await handle_flood_wait(client.get_me)
        if not me:
            logger.error(f"Failed to get bot info for user {user_id} in require_token.", exc_info=True)
            await handle_flood_wait(message.reply_text, "Sorry, an unexpected error occurred. Please try again later.", quote=True)
            return False
        deep_link = f"https://t.me/{me.username}?start={temp_token_string}"
        short_url = deep_link

        try:
            short_url_result = await shorten(deep_link)
            if short_url_result:
                short_url = short_url_result
        except Exception as e:
            logger.warning(f"Failed to shorten token link for user {user_id}: {e}. Using full link.", exc_info=True)

        await handle_flood_wait(
            message.reply_text,
            MSG_TOKEN_INVALID,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Activate Access", url=short_url)]
            ]),
            quote=True
        )
        logger.debug(f"Sent temporary token activation link to user {user_id}.")
        return False
    except Exception as e:
        logger.error(f"Error in require_token: {e}", exc_info=True)
        try:
            await handle_flood_wait(message.reply_text, "An error occurred while checking your authorization. Please try again.", quote=True)
        except Exception as inner_e:
            logger.error(f"Failed to send error message to user in require_token: {inner_e}", exc_info=True)
        return False

async def get_shortener_status(client, message: Message):
    try:
        user_id = message.from_user.id if message.from_user else None
        use_shortener = getattr(Var, "SHORTEN_MEDIA_LINKS", False)
        if user_id:
            try:
                if user_id == Var.OWNER_ID or await allowed(user_id):
                    use_shortener = False
            except Exception as e:
                logger.warning(f"Error checking allowed status for user {user_id} in get_shortener_status: {e}. Defaulting shortener behavior.", exc_info=True)
        return use_shortener
    except Exception as e:
        logger.error(f"Error in get_shortener_status: {e}", exc_info=True)
        return getattr(Var, "SHORTEN_MEDIA_LINKS", False)

async def owner_only(client, update):
    try:
        user = None
        if hasattr(update, 'from_user'):
            user = update.from_user
        else:
            logger.error(f"Unsupported update type or missing from_user in owner_only: {type(update)}", exc_info=True)
            return False

        if not user or user.id != Var.OWNER_ID:
            if hasattr(update, 'answer'):
                await update.answer(MSG_ERROR_UNAUTHORIZED, show_alert=True)
            logger.warning(f"Unauthorized access attempt by {user.id if user else 'unknown'} to owner_only function.")
            return False

        return True
    except Exception as e:
        logger.error(f"Error in owner_only: {e}", exc_info=True)
        try:
            if hasattr(update, 'answer'):
                await update.answer("An error occurred. Please try again.", show_alert=True)
        except Exception as inner_e:
            logger.error(f"Failed to send error answer in owner_only: {inner_e}", exc_info=True)
        return False
