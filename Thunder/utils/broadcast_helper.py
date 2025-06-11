import asyncio
from typing import Tuple, Optional # Added Optional
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked, PeerIdInvalid
from pyrogram.types import Message
from Thunder.utils.logger import logger

async def send_msg(user_id: int, message: Message) -> Tuple[int, Optional[str]]: # Changed str to Optional[str]
    try:
        await message.forward(chat_id=user_id)
        return 200, None
    except FloodWait as e:
        logger.warning(f"FloodWait sending broadcast to {user_id}. Sleeping {e.value + 1}s. Will not auto-retry this specific user now.")
        await asyncio.sleep(e.value + 1)
        return 429, f"{user_id} : FloodWait ({e.value}s)"
    except InputUserDeactivated:
        logger.info(f"User {user_id} is deactivated.")
        return 400, f"{user_id} : deactivated"
    except UserIsBlocked:
        logger.info(f"User {user_id} blocked the bot.")
        return 400, f"{user_id} : blocked the bot"
    except PeerIdInvalid:
        logger.warning(f"PeerIdInvalid for user {user_id}.")
        return 400, f"{user_id} : user ID invalid"
    except Exception as e:
        logger.error(f"Unhandled error sending broadcast to {user_id}: {e}")
        return 500, f"{user_id} : {str(e)}"
