from typing import Set, Optional, List, Dict
import os
from dotenv import load_dotenv
from Thunder.utils.logger import logger

# Load environment variables from config.env
load_dotenv("config.env")

def str_to_bool(val: str) -> bool:
    return val.lower() in ("true", "1", "t", "y", "yes")

def str_to_int_list(val: str) -> List[int]:
    return [int(x) for x in val.split() if x.isdigit()] if val else []

def str_to_int_set(val: str) -> Set[int]:
    return {int(x) for x in val.split() if x.isdigit()} if val else set()

class Var:

    # Telegram API credentials
    API_ID: int = int(os.getenv("API_ID", ""))
    API_HASH: str = os.getenv("API_HASH", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    
    if not all([API_ID, API_HASH, BOT_TOKEN]):
        logger.critical("Missing required Telegram API configuration")
        raise ValueError("Missing required Telegram API configuration")

    # Bot identity and performance
    NAME: str = os.getenv("NAME", "ThunderF2L")
    SLEEP_THRESHOLD: int = int(os.getenv("SLEEP_THRESHOLD", "30"))
    WORKERS: int = int(os.getenv("WORKERS", "100"))
    TIMEOUT: int = int(os.getenv("TIMEOUT", "90"))

    # Channel for file storage
    BIN_CHANNEL: int = int(os.getenv("BIN_CHANNEL", "0"))
    if not BIN_CHANNEL:
        logger.critical("BIN_CHANNEL is required")
        raise ValueError("BIN_CHANNEL is required")

    # Web server configuration
    PORT: int = int(os.getenv("PORT", "8080"))
    BIND_ADDRESS: str = os.getenv("BIND_ADDRESS", "0.0.0.0")
    PING_INTERVAL: int = int(os.getenv("PING_INTERVAL", "840"))
    NO_PORT: bool = str_to_bool(os.getenv("NO_PORT", "True"))
    CACHE_SIZE: int = int(os.getenv("CACHE_SIZE", "100"))
    
    # Owner details
    OWNER_ID: List[int] = str_to_int_list(os.getenv("OWNER_ID", ""))
    if not OWNER_ID:
        logger.warning("WARNING: OWNER_ID is empty. No user will have admin access.")
    OWNER_USERNAME: str = os.getenv("OWNER_USERNAME", "")
    
    # Domain and URL configuration
    FQDN: str = os.getenv("FQDN", "") or BIND_ADDRESS
    HAS_SSL: bool = str_to_bool(os.getenv("HAS_SSL", "False"))
    PROTOCOL: str = "https" if HAS_SSL else "http"
    PORT_SEGMENT: str = "" if NO_PORT else f":{PORT}"
    URL: str = f"{PROTOCOL}://{FQDN}{PORT_SEGMENT}/"
    
    # Database configuration
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    if not DATABASE_URL:
        logger.critical("DATABASE_URL is required")
        raise ValueError("DATABASE_URL is required")

    # Channel configurations
    BANNED_CHANNELS: Set[int] = str_to_int_set(os.getenv("BANNED_CHANNELS", ""))
    
    # Multi-client support flag
    MULTI_CLIENT: bool = False

    # Force channel configuration
    FORCE_CHANNEL_ID: Optional[int] = None
    force_channel_env = os.getenv("FORCE_CHANNEL_ID", "").strip()
    if force_channel_env:
        try:
            FORCE_CHANNEL_ID = int(force_channel_env)
        except ValueError:
            logger.warning(f"Invalid FORCE_CHANNEL_ID '{force_channel_env}' in environment; must be an integer.")

    # Token System
    TOKEN_ENABLED: bool = str_to_bool(os.getenv("TOKEN_ENABLED", "False"))
    TOKEN_TTL_HOURS: int = int(os.getenv("TOKEN_TTL_HOURS", "24"))
    
    # URL Shortener
    SHORTEN_ENABLED: bool = str_to_bool(os.getenv("SHORTEN_ENABLED", "False"))
    SHORTEN_MEDIA_LINKS: bool = str_to_bool(os.getenv("SHORTEN_MEDIA_LINKS", "False"))
    URL_SHORTENER_API_KEY: str = os.getenv("URL_SHORTENER_API_KEY", "")
    URL_SHORTENER_SITE: str = os.getenv("URL_SHORTENER_SITE", "")
