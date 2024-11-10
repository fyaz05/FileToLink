# Thunder/utils/keepalive.py

import asyncio
import aiohttp
from Thunder.vars import Var
from Thunder.utils.logger import logger


async def ping_server():
    """
    Periodically pings the server to keep it alive.

    This function sends a GET request to the server URL at regular intervals defined by PING_INTERVAL.
    It logs the response status or any errors encountered during the request.
    """
    sleep_time = Var.PING_INTERVAL
    while True:
        await asyncio.sleep(sleep_time)
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(Var.URL) as resp:
                    logger.info(f"Pinged server with response: {resp.status}")
        except asyncio.TimeoutError:
            logger.warning("Couldn't connect to the site URL due to timeout.")
        except Exception as e:
            logger.error(f"An error occurred while pinging the server: {e}", exc_info=True)
