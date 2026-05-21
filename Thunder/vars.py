# Thunder/vars.py

import os

from dotenv import load_dotenv

from Thunder.utils.logger import logger

load_dotenv("config.env")

def str_to_bool(val: str) -> bool:
    return val.lower() in ("true", "1", "t", "y", "yes")

def str_to_int_set(val: str) -> set[int]:
    if not val:
        return set()
    result: set[int] = set()
    for x in val.split():
        try:
            result.add(int(x))
        except (TypeError, ValueError):
            continue
    return result


def _safe_int(env_key: str, default: int) -> int:
    raw = os.getenv(env_key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"Non-numeric value '{raw}' for {env_key}, using default {default}")
        return default


class Var:
    API_ID: int = _safe_int("API_ID", 0)
    API_HASH: str = os.getenv("API_HASH", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    if not all([API_ID, API_HASH, BOT_TOKEN]):
        logger.critical("Missing required Telegram API configuration")
        raise ValueError("Missing required Telegram API configuration")

    NAME: str = os.getenv("NAME", "ThunderF2L")
    SLEEP_THRESHOLD: int = _safe_int("SLEEP_THRESHOLD", 600)
    WORKERS: int = _safe_int("WORKERS", 8)

    BIN_CHANNEL: int = _safe_int("BIN_CHANNEL", 0)

    if not BIN_CHANNEL:
        logger.critical("BIN_CHANNEL is required")
        raise ValueError("BIN_CHANNEL is required")

    PORT: int = _safe_int("PORT", 8080)
    BIND_ADDRESS: str = os.getenv("BIND_ADDRESS", "0.0.0.0")
    PING_INTERVAL: int = _safe_int("PING_INTERVAL", 840)
    NO_PORT: bool = str_to_bool(os.getenv("NO_PORT", "True"))

    OWNER_ID: int = _safe_int("OWNER_ID", 0)

    if not OWNER_ID:
        logger.warning("WARNING: OWNER_ID is not set. No user will be granted owner access.")

    FQDN: str = os.getenv("FQDN", "") or BIND_ADDRESS
    HAS_SSL: bool = str_to_bool(os.getenv("HAS_SSL", "True"))
    PROTOCOL: str = "https" if HAS_SSL else "http"
    PORT_SEGMENT: str = "" if NO_PORT else f":{PORT}"
    URL: str = f"{PROTOCOL}://{FQDN}{PORT_SEGMENT}/"

    SET_COMMANDS: bool = str_to_bool(os.getenv("SET_COMMANDS", "True"))

    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    if not DATABASE_URL:
        logger.critical("DATABASE_URL is required")
        raise ValueError("DATABASE_URL is required")

    MAX_BATCH_FILES: int = _safe_int("MAX_BATCH_FILES", 50)

    CHANNEL: bool = str_to_bool(os.getenv("CHANNEL", "False"))

    BANNED_CHANNELS: set[int] = str_to_int_set(os.getenv("BANNED_CHANNELS", ""))

    MULTI_CLIENT: bool = False

    FORCE_CHANNEL_ID: int | None = None

    force_channel_env = os.getenv("FORCE_CHANNEL_ID", "").strip()

    if force_channel_env:
        try:
            FORCE_CHANNEL_ID = int(force_channel_env)
        except ValueError:
            logger.warning(f"Invalid FORCE_CHANNEL_ID '{force_channel_env}' in environment; must be an integer.")

    TOKEN_ENABLED: bool = str_to_bool(os.getenv("TOKEN_ENABLED", "False"))
    TOKEN_TTL_HOURS: int = _safe_int("TOKEN_TTL_HOURS", 24)

    SHORTEN_ENABLED: bool = str_to_bool(os.getenv("SHORTEN_ENABLED", "False"))
    SHORTEN_MEDIA_LINKS: bool = str_to_bool(os.getenv("SHORTEN_MEDIA_LINKS", "False"))
    URL_SHORTENER_API_KEY: str = os.getenv("URL_SHORTENER_API_KEY", "")
    URL_SHORTENER_SITE: str = os.getenv("URL_SHORTENER_SITE", "")

    GLOBAL_RATE_LIMIT: bool = str_to_bool(os.getenv("GLOBAL_RATE_LIMIT", "False"))
    MAX_GLOBAL_REQUESTS_PER_MINUTE: int = _safe_int("MAX_GLOBAL_REQUESTS_PER_MINUTE", 4)

    RATE_LIMIT_ENABLED: bool = str_to_bool(os.getenv("RATE_LIMIT_ENABLED", "False"))
    MAX_FILES_PER_PERIOD: int = _safe_int("MAX_FILES_PER_PERIOD", 2)
    RATE_LIMIT_PERIOD_MINUTES: int = _safe_int("RATE_LIMIT_PERIOD_MINUTES", 1)
    MAX_QUEUE_SIZE: int = _safe_int("MAX_QUEUE_SIZE", 100)

    ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "")
