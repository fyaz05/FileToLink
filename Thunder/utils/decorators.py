from functools import wraps
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from Thunder.vars import Var
from Thunder.utils.messages import (
    MSG_DECORATOR_BANNED,
    MSG_TOKEN_INVALID,
    MSG_ERROR_UNAUTHORIZED
)
from Thunder.utils.database import db
from Thunder.utils.logger import logger
from Thunder.utils.tokens import check, allowed, generate
from Thunder.utils.shortener import shorten

def _check_message_user(message):
    """Helper to validate message and user."""
    if not message.from_user:
        return None
    return message.from_user.id

def check_banned(func):
    @wraps(func)
    async def wrapper(client, message: Message, *args, **kwargs):
        user_id = _check_message_user(message)
        if not user_id:
            return await func(client, message, *args, **kwargs)
            
        if user_id in Var.OWNER_ID:
            return await func(client, message, *args, **kwargs)
            
        ban_details = await db.is_user_banned(user_id)
        if ban_details:
            banned_at = ban_details.get('banned_at')
            if banned_at and hasattr(banned_at, 'strftime'):
                ban_time = banned_at.strftime('%B %d, %Y, %I:%M %p UTC')
            else:
                ban_time = str(banned_at) if banned_at else 'N/A'
            
            ban_message = MSG_DECORATOR_BANNED.format(
                reason=ban_details.get('reason', 'Not specified'),
                ban_time=ban_time
            )
            await message.reply_text(ban_message, quote=True)
            return
            
        return await func(client, message, *args, **kwargs)
    return wrapper

def require_token(func):
    @wraps(func)
    async def wrapper(client, message: Message, *args, **kwargs):
        user_id = _check_message_user(message)
        if not user_id:
            return await func(client, message, *args, **kwargs)
        
        if not getattr(Var, "TOKEN_ENABLED", False):
            return await func(client, message, *args, **kwargs)
        
        if user_id in Var.OWNER_ID:
            return await func(client, message, *args, **kwargs)
        
        if await allowed(user_id):
            return await func(client, message, *args, **kwargs)
        
        if not await check(user_id):
            token_data = await generate(user_id)
            me = await client.get_me()
            deep_link = f"https://t.me/{me.username}?start={token_data['token']}"
            
            try:
                short_url = await shorten(deep_link)
            except Exception as e:
                logger.error(f"URL shortening failed: {e}")
                short_url = deep_link

            await message.reply_text(
                MSG_TOKEN_INVALID,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Get Token", url=short_url)]]
                ),
                quote=True
            )
            return
        
        return await func(client, message, *args, **kwargs)
    return wrapper

def shorten_link(func):
    @wraps(func)
    async def wrapper(client, message: Message, *args, **kwargs):
        user_id = None
        if message.from_user:
            user_id = message.from_user.id

        if user_id and (user_id in Var.OWNER_ID or await allowed(user_id)):
            kwargs['shortener'] = False
        else:
            kwargs['shortener'] = getattr(Var, "SHORTEN_MEDIA_LINKS", False)
        
        return await func(client, message, *args, **kwargs)
    return wrapper


def get_owner_ids():
    """Helper function to safely get owner IDs."""
    owner_ids = Var.OWNER_ID if hasattr(Var, 'OWNER_ID') and Var.OWNER_ID is not None else []
    if isinstance(owner_ids, int):
        owner_ids = [owner_ids]
    return owner_ids


def owner_only(func):
    """Decorator to restrict access to bot owners."""
    async def wrapper(client, callback_query):
        owner_ids = get_owner_ids()
        if callback_query.from_user.id not in owner_ids:
            await callback_query.answer(MSG_ERROR_UNAUTHORIZED, show_alert=True)
            return
        await func(client, callback_query)
    return wrapper
