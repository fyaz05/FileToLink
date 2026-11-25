"""Periodically pings the public URL so a host that idles on traffic stays up."""

import asyncio
import aiohttp
from Thunder.vars import Var
from Thunder.utils.logger import logger


async def ping_server():
    """Ping forever; isolate each attempt so one DNS failure cannot stop keepalive."""
    session = None
    try:
        while True:
            try:
                await asyncio.sleep(Var.PING_INTERVAL)
                if session is None:
                    session = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=10)
                    )
                async with session.get(Var.URL) as resp:
                    if resp.status != 200:
                        logger.warning(f"Ping to {Var.URL} returned status {resp.status}.")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Drop the session in case it is the broken part; retry next interval.
                logger.warning(
                    f"Ping to {Var.URL} failed ({type(e).__name__}: {e}). "
                    f"Retrying in {Var.PING_INTERVAL}s.")
                if session is not None:
                    try:
                        await session.close()
                    except Exception:
                        pass
                    session = None
    finally:
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass
