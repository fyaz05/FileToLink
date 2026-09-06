# Thunder/utils/keepalive.py

import asyncio
import os

import aiohttp

from Thunder.utils.logger import logger
from Thunder.vars import Var


def _health_url() -> str:
    """Ping /health on ourselves (M3).

    The historical implementation GET ``Var.URL``, whose root handler is a
    302 to GitHub -- so the keepalive had been validating GitHub, not this
    bot.  We now bind to the configured address explicitly and check the
    status code.
    """
    fqdn = os.getenv("KEEPALIVE_HOST") or Var.BIND_ADDRESS
    if fqdn in ("0.0.0.0", "::"):  # nosec B104 -- string check mapping bind-all to loopback
        fqdn = "127.0.0.1"
    # self-check always targets loopback over plain HTTP unless overridden
    return f"http://{fqdn}:{Var.PORT}/health"


async def ping_server():
    try:
        url = _health_url()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            while True:
                try:
                    await asyncio.sleep(Var.PING_INTERVAL)
                    async with session.get(url) as resp:
                        body = await resp.text()
                        if resp.status != 200:
                            logger.warning(
                                f"Health check to {url} returned status {resp.status}: {body[:120]}"
                            )
                        else:
                            logger.debug("Health check OK")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"Health check failed: {e}")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Error in ping_server: {e}", exc_info=True)
