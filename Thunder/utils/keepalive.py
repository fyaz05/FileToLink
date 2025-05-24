# Thunder/utils/keepalive.py

import asyncio
import aiohttp
from Thunder.vars import Var
from Thunder.utils.logger import logger

async def ping_server():
    # Periodically pings server URL to keep it alive
    sleep_time = Var.PING_INTERVAL
    
    while True:
        await asyncio.sleep(sleep_time)
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)            ) as session:
                async with session.get(Var.URL) as resp:
                    pass
        except asyncio.TimeoutError:
            logger.warning("Server ping timeout")
        except Exception as e:
            logger.error(f"Ping error: {str(e)}")
