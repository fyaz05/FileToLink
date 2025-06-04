# Thunder/utils/decorators.py

from functools import wraps
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from Thunder.vars import Var
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_DECORATOR_BANNED,
    MSG_TOKEN_INVALID,
    MSG_ERROR_UNAUTHORIZED
)
from Thunder.utils.database import db
from Thunder.utils.tokens import check, allowed, generate
from Thunder.utils.shortener import shorten


def check_banned(func):
    @wraps(func)
    async def wrapper(client, message: Message, *args, **kwargs):
        try:
            if not message.from_user:
                return await func(client, message, *args, **kwargs)
            user_id = message.from_user.id
            if isinstance(Var.OWNER_ID, int) and user_id == Var.OWNER_ID:
                 return await func(client, message, *args, **kwargs)
            elif isinstance(Var.OWNER_ID, (list, tuple, set)) and user_id in Var.OWNER_ID:
                 return await func(client, message, *args, **kwargs)

            ban_details = await db.is_user_banned(user_id)
            if ban_details:
                banned_at = ban_details.get('banned_at')
                ban_time = (
                    banned_at.strftime('%B %d, %Y, %I:%M %p UTC')
                    if banned_at and hasattr(banned_at, 'strftime')
                    else str(banned_at) if banned_at else 'N/A'
                )
                await message.reply_text(
                    MSG_DECORATOR_BANNED.format(
                        reason=ban_details.get('reason', 'Not specified'),
                        ban_time=ban_time
                    ),
                    quote=True
                )
                logger.info(f"Blocked banned user {user_id} from accessing {func.__name__}.")
                return
            return await func(client, message, *args, **kwargs)
        except Exception as e:
            logger.error(f"Error in check_banned decorator for {func.__name__}: {e}")
            return await func(client, message, *args, **kwargs)
    return wrapper

def require_token(func):
    @wraps(func)
    async def wrapper(client, message: Message, *args, **kwargs):
        try:
            if not message.from_user:
                return await func(client, message, *args, **kwargs)

            if not getattr(Var, "TOKEN_ENABLED", False):
                return await func(client, message, *args, **kwargs)

            user_id = message.from_user.id
            is_owner = (isinstance(Var.OWNER_ID, int) and user_id == Var.OWNER_ID) or \
                       (isinstance(Var.OWNER_ID, (list, tuple, set)) and user_id in Var.OWNER_ID)

            if is_owner or await allowed(user_id) or await check(user_id):
                return await func(client, message, *args, **kwargs)

            temp_token_string = None
            try:
                temp_token_string = await generate(user_id)
            except Exception as e:
                logger.error(f"Failed to generate temporary token for user {user_id} in require_token: {e}")
                await message.reply_text("Sorry, could not generate an access token link. Please try again later.", quote=True)
                return

            if not temp_token_string:
                logger.error(f"Temporary token generation returned empty for user {user_id} in require_token.")
                await message.reply_text("Sorry, could not generate an access token link. Please try again later.", quote=True)
                return

            me = await client.get_me()
            deep_link = f"https://t.me/{me.username}?start={temp_token_string}"
            short_url = deep_link

            try:
                short_url_result = await shorten(deep_link)
                if short_url_result:
                    short_url = short_url_result
            except Exception as e:
                logger.warning(f"Failed to shorten token link for user {user_id}: {e}. Using full link.")

            await message.reply_text(
                MSG_TOKEN_INVALID,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Activate Access", url=short_url)]
                ]),
                quote=True
            )
            logger.debug(f"Sent temporary token activation link to user {user_id} for {func.__name__}.")
            return
        except Exception as e:
            logger.error(f"Error in require_token decorator for {func.__name__}: {e}")
            try:
                await message.reply_text("An error occurred while checking your authorization. Please try again.", quote=True)
            except Exception as inner_e:
                logger.error(f"Failed to send error message to user in require_token: {inner_e}")
            return
    return wrapper

def shorten_link(func):
    @wraps(func)
    async def wrapper(client, message: Message, *args, **kwargs):
        try:
            user_id = message.from_user.id if message.from_user else None
            use_shortener = getattr(Var, "SHORTEN_MEDIA_LINKS", False)
            if user_id:
                try:
                    is_owner = (isinstance(Var.OWNER_ID, int) and user_id == Var.OWNER_ID) or \
                               (isinstance(Var.OWNER_ID, (list, tuple, set)) and user_id in Var.OWNER_ID)
                    if is_owner or await allowed(user_id):
                        use_shortener = False
                except Exception as e:
                    logger.warning(f"Error checking allowed status for user {user_id} in shorten_link: {e}. Defaulting shortener behavior.")

            kwargs['shortener'] = use_shortener
            return await func(client, message, *args, **kwargs)
        except Exception as e:
            logger.error(f"Error in shorten_link decorator for {func.__name__}: {e}")
            kwargs['shortener'] = getattr(Var, "SHORTEN_MEDIA_LINKS", False)
            return await func(client, message, *args, **kwargs)
    return wrapper

def owner_only(func):
    _cached_owner_ids = None
    @wraps(func)
    async def wrapper(client, callback_query: Message):
        nonlocal _cached_owner_ids
        try:
            if _cached_owner_ids is None:
                owner_ids_config = getattr(Var, 'OWNER_ID', [])
                if isinstance(owner_ids_config, int):
                    _cached_owner_ids = {owner_ids_config}
                elif isinstance(owner_ids_config, (list, tuple, set)):
                    _cached_owner_ids = set(owner_ids_config)
                else:
                    _cached_owner_ids = set()

            if callback_query.from_user.id not in _cached_owner_ids:
                await callback_query.answer(MSG_ERROR_UNAUTHORIZED, show_alert=True)
                logger.warning(f"Unauthorized access attempt by {callback_query.from_user.id} to owner_only function {func.__name__}.")
                return

            return await func(client, callback_query)
        except Exception as e:
            logger.error(f"Error in owner_only decorator for {func.__name__}: {e}")
            try:
                await callback_query.answer("An error occurred. Please try again.", show_alert=True)
            except Exception as inner_e:
                logger.error(f"Failed to send error answer in owner_only: {inner_e}")
            return
    return wrapper
