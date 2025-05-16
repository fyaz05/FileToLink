"""
Integrates with the Shortzy Python client library.
"""
import asyncio
from shortzy import Shortzy
from Thunder.vars import Var
from Thunder.utils.logger import logger

# Initialize Shortzy client (optional base_site)
shortzy_site = Var.SHORTZY_SITE if Var.SHORTZY_SITE else None
if shortzy_site:
    shortzy_client = Shortzy(api_key=Var.SHORTZY_KEY, base_site=shortzy_site)
else:
    shortzy_client = Shortzy(api_key=Var.SHORTZY_KEY)

async def shorten(url: str) -> str:
    """
    Shorten a URL using the Shortzy client library.
    Falls back to original URL on failure.
    """
    # Check if any shortening is enabled (token or media links) and if we have an API key
    if not (getattr(Var, "SHORTEN_ENABLED", False) or 
            getattr(Var, "SHORTEN_MEDIA_LINKS", False)) or not Var.SHORTZY_KEY:
        return url

    try:
        return await shortzy_client.convert(url)
    except Exception as e:
        logger.error(f"Shortzy convert error: {e}")
        return url
