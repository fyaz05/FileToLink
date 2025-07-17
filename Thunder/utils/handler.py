# Thunder/utils/handler.py

import asyncio
from typing import Callable

from pyrogram.errors import FloodWait

from Thunder.utils.logger import logger


async def handle_flood_wait(func: Callable, *args, **kwargs):
    retries = kwargs.pop('retries', 3)
    delay = kwargs.pop('delay', 3)
    
    for i in range(retries):
        try:
            return await func(*args, **kwargs)
        except FloodWait as e:
            wait_time = e.value
            logger.debug(f"FloodWait encountered in '{func.__name__}'. Waiting for {wait_time}s. Retry {i + 1}/{retries}.")
            await asyncio.sleep(wait_time)
        except Exception:
            logger.error(f"An exception occurred in '{func.__name__}' on retry {i + 1}/{retries}", exc_info=True)
            if i < retries - 1:
                await asyncio.sleep(delay)
            else:
                raise
    return None
