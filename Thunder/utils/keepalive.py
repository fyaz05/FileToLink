# Thunder/utils/keepalive.py

import asyncio
import aiohttp
from Thunder.vars import Var
from Thunder.utils.logger import logger
from Thunder.utils.error_handling import log_errors

@log_errors
async def ping_server():
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=10)
    ) as session:
        while True:
            await asyncio.sleep(Var.PING_INTERVAL)
            async with session.get(Var.URL) as resp:
                if resp.status != 200:
                    logger.warning(f"Ping to {Var.URL} returned status {resp.status}.")
