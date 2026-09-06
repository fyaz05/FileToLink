# Thunder/vars.py

"""Central configuration (plan M6).

Boot behaviour:

* loads ``config.env`` then ``config.env.local`` (local layer wins);
* validates **all** variables and prints every problem together before
  failing -- instead of the historical first-bad-``int()`` traceback;
* names the offending variable on conversion failure and enforces bounds;
* hard-fails on missing ``OWNER_ID`` (plan H7 -- the old warning meant every
  owner check silently matched nobody).

The ``Var`` facade is kept so no import site changes.
"""

import os
from typing import List, Optional, Set

from dotenv import load_dotenv

from Thunder.utils.logger import logger

load_dotenv("config.env")
load_dotenv("config.env.local")  # optional local override layer


def str_to_bool(val: str) -> bool:
    return val.lower() in ("true", "1", "t", "y", "yes")


def str_to_int_set(val: str) -> Set[int]:
    if not val:
        return set()
    result: Set[int] = set()
    for x in val.split():
        try:
            result.add(int(x))
        except (TypeError, ValueError):
            continue
    return result


_config_errors: List[str] = []
_config_warnings: List[str] = []


def _get_int(name: str, default: str, *, min_val: Optional[int] = None,
             max_val: Optional[int] = None) -> int:
    raw = os.getenv(name, default)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        _config_errors.append(f"{name}={raw!r} is not a valid integer")
        return int(default)
    if min_val is not None and value < min_val:
        _config_errors.append(f"{name}={value} must be >= {min_val}")
    if max_val is not None and value > max_val:
        _config_errors.append(f"{name}={value} must be <= {max_val}")
    return value


def _get_float(name: str, default: str, *, min_val: Optional[float] = None) -> float:
    raw = os.getenv(name, default)
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        _config_errors.append(f"{name}={raw!r} is not a valid number")
        return float(default)
    if min_val is not None and value < min_val:
        _config_errors.append(f"{name}={value} must be >= {min_val}")
    return value


def _require(value: object, name: str, what: str) -> None:
    if not value:
        _config_errors.append(f"{name} is required ({what})")


class Var:
    # ---- Required Telegram configuration ----
    API_ID: int = _get_int("API_ID", "0", min_val=1)
    API_HASH: str = os.getenv("API_HASH", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    _require(API_ID, "API_ID", "numeric app id from my.telegram.org")
    _require(API_HASH, "API_HASH", "app hash from my.telegram.org")
    _require(BOT_TOKEN, "BOT_TOKEN", "bot token from @BotFather")

    NAME: str = os.getenv("NAME", "ThunderF2L")
    SLEEP_THRESHOLD: int = _get_int("SLEEP_THRESHOLD", "600", min_val=0)
    WORKERS: int = _get_int("WORKERS", "8", min_val=1, max_val=64)

    BIN_CHANNEL: int = _get_int("BIN_CHANNEL", "0")
    _require(BIN_CHANNEL, "BIN_CHANNEL", "storage channel id, e.g. -1001234567890")

    PORT: int = _get_int("PORT", "8080", min_val=1, max_val=65535)
    BIND_ADDRESS: str = os.getenv("BIND_ADDRESS", "0.0.0.0")
    PING_INTERVAL: int = _get_int("PING_INTERVAL", "840", min_val=30)
    NO_PORT: bool = str_to_bool(os.getenv("NO_PORT", "True"))

    # H7: missing OWNER_ID is fatal -- every owner check matched nobody before.
    OWNER_ID: int = _get_int("OWNER_ID", "0", min_val=1)
    _require(OWNER_ID, "OWNER_ID", "your Telegram user id (get from @userinfobot)")

    FQDN: str = os.getenv("FQDN", "") or BIND_ADDRESS
    if os.getenv("FQDN", "") == "":
        _config_warnings.append(
            "FQDN is not set; generated links will use the bind address and "
            "will not be reachable from outside this machine."
        )
    HAS_SSL: bool = str_to_bool(os.getenv("HAS_SSL", "True"))
    PROTOCOL: str = "https" if HAS_SSL else "http"
    PORT_SEGMENT: str = "" if NO_PORT else f":{PORT}"
    URL: str = f"{PROTOCOL}://{FQDN}{PORT_SEGMENT}/"

    SET_COMMANDS: bool = str_to_bool(os.getenv("SET_COMMANDS", "True"))

    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    _require(DATABASE_URL, "DATABASE_URL", "MongoDB connection string")

    MAX_BATCH_FILES: int = _get_int("MAX_BATCH_FILES", "50", min_val=1, max_val=100)

    CHANNEL: bool = str_to_bool(os.getenv("CHANNEL", "False"))
    BANNED_CHANNELS: Set[int] = str_to_int_set(os.getenv("BANNED_CHANNELS", ""))

    # Kept for backward compatibility of env parsing; no longer read at runtime.
    MULTI_CLIENT: bool = False

    FORCE_CHANNEL_ID: Optional[int] = None
    force_channel_env = os.getenv("FORCE_CHANNEL_ID", "").strip()
    if force_channel_env:
        try:
            FORCE_CHANNEL_ID = int(force_channel_env)
        except ValueError:
            _config_errors.append(
                f"FORCE_CHANNEL_ID={force_channel_env!r} must be an integer"
            )

    TOKEN_ENABLED: bool = str_to_bool(os.getenv("TOKEN_ENABLED", "False"))
    TOKEN_TTL_HOURS: int = _get_int("TOKEN_TTL_HOURS", "24", min_val=1)

    SHORTEN_ENABLED: bool = str_to_bool(os.getenv("SHORTEN_ENABLED", "False"))
    SHORTEN_MEDIA_LINKS: bool = str_to_bool(os.getenv("SHORTEN_MEDIA_LINKS", "False"))
    URL_SHORTENER_API_KEY: str = os.getenv("URL_SHORTENER_API_KEY", "")
    URL_SHORTENER_SITE: str = os.getenv("URL_SHORTENER_SITE", "")
    if (SHORTEN_ENABLED or SHORTEN_MEDIA_LINKS) and not (URL_SHORTENER_SITE and URL_SHORTENER_API_KEY):
        _config_warnings.append(
            "Shortener enabled but URL_SHORTENER_SITE/URL_SHORTENER_API_KEY "
            "missing; links will not be shortened."
        )

    GLOBAL_RATE_LIMIT: bool = str_to_bool(os.getenv("GLOBAL_RATE_LIMIT", "False"))
    MAX_GLOBAL_REQUESTS_PER_MINUTE: int = _get_int(
        "MAX_GLOBAL_REQUESTS_PER_MINUTE", "4", min_val=1)
    GLOBAL_RPS_LIMIT: float = _get_float(
        "GLOBAL_RPS_LIMIT", "0", min_val=0)  # 0 = derive from per-minute value

    RATE_LIMIT_ENABLED: bool = str_to_bool(os.getenv("RATE_LIMIT_ENABLED", "False"))
    MAX_FILES_PER_PERIOD: int = _get_int("MAX_FILES_PER_PERIOD", "2", min_val=1)
    RATE_LIMIT_PERIOD_MINUTES: int = _get_int("RATE_LIMIT_PERIOD_MINUTES", "1", min_val=1)
    MAX_QUEUE_SIZE: int = _get_int("MAX_QUEUE_SIZE", "100", min_val=1)

    # ---- New knobs introduced by the improvement plan ----

    # M12: allowlist mode -- only owner + authorized users may use the bot.
    PRIVATE_MODE: bool = str_to_bool(os.getenv("PRIVATE_MODE", "False"))

    # L1: legacy /watch/{hash}{id} URL family (default on; removal planned).
    ENABLE_LEGACY_LINKS: bool = str_to_bool(os.getenv("ENABLE_LEGACY_LINKS", "True"))

    # L10: /shell kill-switch -- powerful owner command is opt-in.
    ENABLE_SHELL: bool = str_to_bool(os.getenv("ENABLE_SHELL", "False"))

    # L2: optional expiry for file records (0 = keep forever).
    FILE_TTL_DAYS: int = _get_int("FILE_TTL_DAYS", "0", min_val=0, max_val=3650)

    # H6b: queue executor worker pool.
    EXECUTOR_WORKERS: int = _get_int("EXECUTOR_WORKERS", "5", min_val=1, max_val=32)

    # M4: worker pools for broadcast / batch.
    BROADCAST_WORKERS: int = _get_int("BROADCAST_WORKERS", "4", min_val=1, max_val=16)
    BATCH_WORKERS: int = _get_int("BATCH_WORKERS", "5", min_val=1, max_val=16)

    # M9: per-client admission cap for the streaming server.
    MAX_CONCURRENT_STREAMS: int = _get_int("MAX_CONCURRENT_STREAMS", "8", min_val=1)

    # M14: touch-buffer flush cadence + cap.
    TOUCH_FLUSH_SECONDS: int = _get_int("TOUCH_FLUSH_SECONDS", "3", min_val=1, max_val=60)
    TOUCH_BUFFER_MAX: int = _get_int("TOUCH_BUFFER_MAX", "1000", min_val=100)

    # H8: default wall-clock budget for lightweight Telegram RPCs.
    TG_RPC_TIMEOUT_SECONDS: float = _get_float("TG_RPC_TIMEOUT_SECONDS", "30", min_val=0)

    # H10: logging.
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "plain").lower()


if _config_errors:
    logger.critical("Invalid configuration -- %d problem(s) found:" % len(_config_errors))
    for err in _config_errors:
        logger.critical(f"  ✖ {err}")
    raise SystemExit(
        f"Configuration invalid: fix {len(_config_errors)} problem(s) listed above "
        "in config.env / environment and start again."
    )

if _config_warnings:
    for warn in _config_warnings:
        logger.warning(f"  ⚠ {warn}")

mode_note = "PRIVATE" if Var.PRIVATE_MODE else "public"
logger.info(f"Gate mode: {mode_note}; legacy links: {'on' if Var.ENABLE_LEGACY_LINKS else 'off'}")
