"""
Thunder/vars.py - Environment and configuration variables for the Thunder bot.
"""

import os
from typing import Set, Optional, List

from dotenv import load_dotenv
from Thunder.utils.logger import logger

load_dotenv()

def str2bool(value: str) -> bool:
    """Convert string to boolean value."""
    return value.lower() in ("true", "1", "yes", "y", "t")


class Var:
    """Configuration variables for the Thunder bot."""
    # Telegram API credentials
    API_ID: int = int(os.getenv("API_ID", ""))
    API_HASH: str = os.getenv("API_HASH", "")

    # Bot token and identity
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    NAME: str = os.getenv("NAME", "ThunderF2L")

    # Performance settings
    SLEEP_THRESHOLD: int = int(os.getenv("SLEEP_THRESHOLD", "60"))
    WORKERS: int = int(os.getenv("WORKERS", "100"))

    # Channel for file storage
    BIN_CHANNEL: int = int(os.getenv("BIN_CHANNEL", ""))

    # Web server configuration
    PORT: int = int(os.getenv("PORT", "460"))
    BIND_ADDRESS: str = os.getenv("WEB_SERVER_BIND_ADDRESS", "0.0.0.0")
    PING_INTERVAL: int = int(os.getenv("PING_INTERVAL", "1200"))  # 20 minutes
    NO_PORT: bool = str2bool(os.getenv("NO_PORT", "True"))
    CACHE_SIZE: int = int(os.getenv("CACHE_SIZE", "100"))

    # Owner details
    OWNER_ID: List[int] = [
        int(x) for x in os.getenv("OWNER_ID", "").split() if x.isdigit()
    ]
    OWNER_USERNAME: str = os.getenv("OWNER_USERNAME", "")

    # Deployment configuration
    APP_NAME: str = os.getenv("APP_NAME", "")
    ON_HEROKU: bool = "DYNO" in os.environ

    # Domain name configuration
    if ON_HEROKU:
        FQDN: str = os.getenv("FQDN", "") or f"{APP_NAME}.herokuapp.com"
    else:
        FQDN: str = os.getenv("FQDN", BIND_ADDRESS)

    # SSL and URL configuration
    HAS_SSL: bool = str2bool(os.getenv("HAS_SSL", "True"))
    URL: str = f"https://{FQDN}/" if HAS_SSL else f"http://{FQDN}/"

    # Database configuration
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # Channel configurations
    BANNED_CHANNELS: Set[int] = set(
        int(x)
        for x in os.getenv("BANNED_CHANNELS", "").split()
        if x.lstrip("-").isdigit()
    )

    # Multi-client support flag
    MULTI_CLIENT: bool = False

    # Force channel configuration
    FORCE_CHANNEL_ID: Optional[int] = None
    _force_channel_id = os.environ.get("FORCE_CHANNEL_ID")
    if _force_channel_id:
        try:
            FORCE_CHANNEL_ID = int(_force_channel_id)
        except ValueError:
            logger.warning(
                f"Invalid FORCE_CHANNEL_ID: {_force_channel_id}. "
                f"Must be an integer. Disabling force channel."
            )
