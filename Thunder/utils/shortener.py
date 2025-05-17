"""
Integrates with the Shortzy Python client library.
"""
import asyncio
from typing import Optional, Union
from shortzy import Shortzy
from Thunder.vars import Var
from Thunder.utils.logger import logger


# Global client instance
_shortzy_client = None

def _get_shortzy_client() -> Optional[Shortzy]:
    """Get or initialize the Shortzy client."""
    global _shortzy_client

    if _shortzy_client is not None:
        return _shortzy_client

    # Check if shortening is enabled
    is_enabled = getattr(Var, "SHORTEN_ENABLED", False) or getattr(Var, "SHORTEN_MEDIA_LINKS", False)
    if not is_enabled:
        logger.info("ⓘ URL shortening is disabled")
        return None

    # Check if API key is provided
    if not Var.SHORTZY_KEY:
        logger.warning("ⓘ Shortzy URL shortener disabled (API key not provided)")
        return None

    # Initialize Shortzy
    try:
        # Configure optional base site
        base_site = Var.SHORTZY_SITE or None
        client_kwargs = {"api_key": Var.SHORTZY_KEY}
        if base_site:
            client_kwargs["base_site"] = base_site

        # Create client and cache it
        _shortzy_client = Shortzy(**client_kwargs)
        logger.info("✓ Shortzy URL shortener initialized successfully")
        return _shortzy_client
    except Exception as e:
        logger.error(f"✖ Failed to initialize Shortzy URL shortener: {e}")
        return None

async def shorten(url: str) -> str:
    """
    Shorten a URL using the Shortzy client library.
    Falls back to original URL on failure.
    """
    client = _get_shortzy_client()

    # Return original URL if client is not initialized
    if not client:
        return url

    # Try to shorten the URL
    try:
        return await client.convert(url)
    except Exception as e:
        logger.error(f"Shortzy convert error: {e}")
        return url
