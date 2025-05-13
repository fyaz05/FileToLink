from functools import wraps
from datetime import datetime
from typing import Optional, Dict, Any
from ..vars import Var
from . import messages
from Thunder.utils.database import Database

# Initialize database
db = Database(Var.DATABASE_URL, Var.NAME)

def check_banned(func):
    @wraps(func)
    async def wrapper(client, message):
        if not message.from_user:
            return await func(client, message)
            
        user_id = message.from_user.id
        if user_id in Var.OWNER_ID:
            return await func(client, message)
            
        ban_details = await db.is_user_banned(user_id)
        if ban_details:
            banned_at = ban_details.get('banned_at')
            ban_time = banned_at.strftime('%B %d, %Y, %I:%M %p UTC') if banned_at else 'N/A'
            ban_message = messages.MSG_DECORATOR_BANNED.format(
                reason=ban_details.get('reason', 'Not specified'),
                ban_time=ban_time
            )
            await message.reply_text(ban_message, quote=True)
            return
            
        return await func(client, message)
    return wrapper
