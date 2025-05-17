from functools import wraps
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from ..vars import Var
from Thunder.utils.database import Database
from Thunder.utils.logger import logger
from Thunder.utils.tokens import check, allowed, generate
from Thunder.utils.shortener import shorten

# Initialize database
db = Database(Var.DATABASE_URL, Var.NAME)

def check_banned(func):
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        if not message.from_user:
            return await func(client, message, *args, **kwargs)
            
        user_id = message.from_user.id
        if user_id in Var.OWNER_ID:
            return await func(client, message, *args, **kwargs)
            
        ban_details = await db.is_user_banned(user_id)
        if ban_details:
            banned_at = ban_details.get('banned_at')
            ban_time = banned_at.strftime('%B %d, %Y, %I:%M %p UTC') if banned_at else 'N/A'
            ban_message = f"❌ **You are banned from using this bot!**\n\n**Reason:** {ban_details.get('reason', 'Not specified')}\n**Banned on:** {ban_time}"
            await message.reply_text(ban_message, quote=True)
            return
            
        return await func(client, message, *args, **kwargs)
    return wrapper

def require_token(func):
    """
    Decorator to check if user has a valid token. If not, prompts user to get a new token.
    """
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        if not message.from_user:
            return await func(client, message, *args, **kwargs)
            
        user_id = message.from_user.id
        
        # Skip check if token system is disabled
        if not getattr(Var, "TOKEN_ENABLED", False):
            return await func(client, message, *args, **kwargs)
        
        # Skip check for owners
        if user_id in Var.OWNER_ID:
            return await func(client, message, *args, **kwargs)
        
        # Check if user is authorized
        if await allowed(user_id):
            return await func(client, message, *args, **kwargs)
        
        # Check and auto-generate token
        if not await check(user_id):
            # Token is invalid or missing: generate a new one and build a deep-link
            token_data = await generate(user_id)
            # Retrieve bot username from token
            me = await client.get_me()
            deep_link = f"https://t.me/{me.username}?start={token_data['token']}"
            
            # Try to shorten URL with proper error handling
            try:
                short_url = await shorten(deep_link)
            except Exception as e:
                logger.error(f"URL shortening failed: {e}")
                short_url = deep_link  # Use original URL as fallback

            await message.reply_text(
                "❌ **Access Denied!**\n\n"
                "Please get a new token to continue using the bot.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Get New Token", url=short_url)]]
                ),
                quote=True
            )
            return  # Do not proceed with the original function
        
        # Token is valid, continue with the original function
        return await func(client, message, *args, **kwargs)
    
    return wrapper

def shorten_link(func):
    """
    Decorator to control whether links should be shortened.
    When applied, the function will receive a 'shortener' parameter indicating whether
    to shorten the links based on configuration.
    """
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        user_id = None
        if message.from_user:
            user_id = message.from_user.id

        if user_id and (user_id in Var.OWNER_ID or await allowed(user_id)):
            # For owners or authorized users, override global setting and disable shortening
            kwargs['shortener'] = False
        else:
            # For other users, adhere to the global link shortening setting
            kwargs['shortener'] = getattr(Var, "SHORTEN_MEDIA_LINKS", False)
        
        # Call the original function with the shortener flag
        return await func(client, message, *args, **kwargs)
    
    return wrapper
