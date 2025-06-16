# Thunder/utils/broadcast_helper.py

import asyncio
from typing import Tuple, Optional
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked, PeerIdInvalid
from pyrogram.types import Message
from Thunder.utils.logger import logger

async def send_msg(user_id: int, message: Message) -> Tuple[int, Optional[str]]:
    import time
    import psutil
    start_time = time.time()
    start_mem = psutil.Process().memory_info().rss
    
    logger.debug(f"Broadcasting message {message.id} to user {user_id} (size: {len(str(message))} chars)")
    try:
        await message.forward(chat_id=user_id)
        latency = time.time() - start_time
        mem_used = (psutil.Process().memory_info().rss - start_mem) / 1024
        logger.debug(f"Broadcast success to {user_id} in {latency:.2f}s | Memory: +{mem_used:.1f}KB")
        return 200, None
    except FloodWait as e:
        # Pyrogram guarantees e.value is integer for FloodWait errors
        wait_seconds: int = e.value  # type: ignore
        try:
            wait_seconds = int(wait_seconds)
        except (TypeError, ValueError):
            wait_seconds = 10  # Default to 10 seconds if conversion fails
            
        logger.warning(f"FloodWait sending broadcast to {user_id}. Sleeping {wait_seconds}s. Will not auto-retry this specific user now.")
        await asyncio.sleep(wait_seconds)
        return 429, f"{user_id} : FloodWait ({wait_seconds}s)"
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
        latency = time.time() - start_time
        logger.error(f"Unhandled error sending broadcast to {user_id} after {latency:.2f}s: {e}")
        return 500, f"{user_id} : {str(e)}"
