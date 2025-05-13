"""
Thunder/vars.py - Configuration variables for the Thunder bot.
"""

from typing import Set, Optional, List
import config
from Thunder.utils.logger import logger

class Var:
    """Configuration variables for the Thunder bot."""
    # Telegram API credentials
    API_ID: int = getattr(config, "API_ID", 0)
    if API_ID == 0:
        logger.critical("CRITICAL: API_ID is not configured in Thunder/config.py!")
        raise ValueError("CRITICAL: API_ID is not configured in Thunder/config.py!")

    API_HASH: str = getattr(config, "API_HASH", "")
    if not API_HASH:
        logger.critical("CRITICAL: API_HASH is not configured in Thunder/config.py!")
        raise ValueError("CRITICAL: API_HASH is not configured in Thunder/config.py!")

    # Bot token and identity
    BOT_TOKEN: str = getattr(config, "BOT_TOKEN", "")
    if not BOT_TOKEN:
        logger.critical("CRITICAL: BOT_TOKEN is not configured in Thunder/config.py!")
        raise ValueError("CRITICAL: BOT_TOKEN is not configured in Thunder/config.py!")
    NAME: str = getattr(config, "NAME", "ThunderF2L")
    
    # Performance settings
    SLEEP_THRESHOLD: int = getattr(config, "SLEEP_THRESHOLD", 60)
    WORKERS: int = getattr(config, "WORKERS", 100)

    # Channel for file storage
    BIN_CHANNEL: int = getattr(config, "BIN_CHANNEL", 0)
    if BIN_CHANNEL == 0:
        logger.critical("CRITICAL: BIN_CHANNEL is not configured in Thunder/config.py!")
        raise ValueError("CRITICAL: BIN_CHANNEL is not configured in Thunder/config.py!")

    # Web server configuration
    PORT: int = getattr(config, "PORT", 8080)
    BIND_ADDRESS: str = getattr(config, "BIND_ADDRESS", "0.0.0.0")
    PING_INTERVAL: int = getattr(config, "PING_INTERVAL", 1200)
    NO_PORT: bool = getattr(config, "NO_PORT", True)
    CACHE_SIZE: int = getattr(config, "CACHE_SIZE", 100)

    # Owner details
    OWNER_ID: List[int] = getattr(config, "OWNER_ID", [])
    if not OWNER_ID:
        logger.warning("WARNING: OWNER_ID is empty. No user will have admin access.")
    OWNER_USERNAME: str = getattr(config, "OWNER_USERNAME", "")
    
    # Domain and URL configuration
    FQDN: str = getattr(config, "FQDN", None) or BIND_ADDRESS
    HAS_SSL: bool = getattr(config, "HAS_SSL", False)
    PROTOCOL: str = "https" if HAS_SSL else "http"
    PORT_SEGMENT: str = "" if NO_PORT else f":{PORT}"
    URL: str = f"{PROTOCOL}://{FQDN}{PORT_SEGMENT}/"

    # Database configuration
    DATABASE_URL: str = getattr(config, "DATABASE_URL", "")

    # Channel configurations
    BANNED_CHANNELS: Set[int] = getattr(config, "BANNED_CHANNELS", set())

    # Multi-client support flag
    MULTI_CLIENT: bool = False

    # Force channel configuration
    FORCE_CHANNEL_ID: Optional[int] = getattr(config, "FORCE_CHANNEL_ID", None)
