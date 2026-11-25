"""Environment configuration, validated at import."""

from dotenv import load_dotenv
from typing import Optional, Set
from Thunder.utils.config_values import (
    CONFIG_FILE,
    ConfigError,
    CORRECTIONS,
    PROBLEMS,
    bool_env,
    int_env,
    int_set_env,
    is_valid_host,
    mongo_uri_problem,
    normalise_host,
    str_env,
)
from Thunder.utils.logger import logger
from Thunder.utils.public_host import (
    is_bind_address,
    platform_host_source,
    resolve_public_host,
)

_config_path = CONFIG_FILE
load_dotenv(_config_path)


class Var:
    API_ID: int = int_env("API_ID", 0, required=True, minimum=1)
    API_HASH: str = str_env("API_HASH")
    BOT_TOKEN: str = str_env("BOT_TOKEN")

    if not API_HASH:
        PROBLEMS.append("API_HASH is not set")
    if not BOT_TOKEN:
        PROBLEMS.append("BOT_TOKEN is not set")

    NAME: str = str_env("NAME", "ThunderF2L")

    # NAME permits characters MongoDB rejects in a database name (pymongo InvalidName).
    DATABASE_NAME: str = "".join(
        char if char.isalnum() or char in "-_" else "_"
        for char in NAME.strip()
    ) or "Thunder"

    SLEEP_THRESHOLD: int = int_env("SLEEP_THRESHOLD", 600, minimum=0)
    WORKERS: int = int_env("WORKERS", 8, minimum=1)

    # A channel id is always negative; a positive value would resolve to a user
    # chat, and every upload would fail with a confusing copy error.
    BIN_CHANNEL: int = int_env(
        "BIN_CHANNEL", 0, required=True, maximum=-1,
        expectation="must be a channel id such as -1001234567890")

    PORT: int = int_env("PORT", 8080, minimum=1, maximum=65535)
    BIND_ADDRESS: str = str_env("BIND_ADDRESS", "0.0.0.0")
    PING_INTERVAL: int = int_env("PING_INTERVAL", 840, minimum=1)
    # 0 disables the post-boot channel watcher.
    CHANNEL_WATCH_INTERVAL: int = int_env("CHANNEL_WATCH_INTERVAL", 3600, minimum=0)
    NO_PORT: bool = bool_env("NO_PORT", True)

    OWNER_ID: int = int_env("OWNER_ID", 0, minimum=0)

    _fqdn_raw: str = str_env("FQDN")
    _fqdn_host, _fqdn_port, _fqdn_notes = normalise_host(_fqdn_raw)
    FQDN: str
    FQDN_PORT: Optional[int]
    FQDN, FQDN_PORT = resolve_public_host(_fqdn_host, _fqdn_port, BIND_ADDRESS)
    HAS_SSL: bool = bool_env("HAS_SSL", True)
    PROTOCOL: str = "https" if HAS_SSL else "http"
    # An explicit FQDN port is public and wins over the bind port.
    _public_port: Optional[int] = FQDN_PORT or (None if NO_PORT else PORT)
    PORT_SEGMENT: str = f":{_public_port}" if _public_port else ""
    URL: str = f"{PROTOCOL}://{FQDN}{PORT_SEGMENT}/"

    if _fqdn_notes:
        CORRECTIONS.append(f"FQDN ({', '.join(_fqdn_notes)})")

    # Report invalid resolved hosts; bind addresses may lose to platform values.
    _platform_name, _platform_raw = platform_host_source()
    _platform_host = normalise_host(_platform_raw)[0] if _platform_raw else ""
    platform_used = bool(_platform_host) and (
        not _fqdn_host or is_bind_address(_fqdn_host)
    )

    configured_invalid = bool(_fqdn_raw) and (
        not _fqdn_host or (FQDN == _fqdn_host and not is_valid_host(_fqdn_host))
    )
    platform_invalid = bool(platform_used and FQDN and not is_valid_host(FQDN))
    if configured_invalid:
        PROBLEMS.append(
            f"FQDN {_fqdn_raw!r} is not a valid host or host:port. "
            "Use a host name or bracketed IPv6 address with an optional numeric "
            "port (1–65535), without a path.")
    if platform_invalid:
        PROBLEMS.append(
            f"{_platform_name or 'platform host'} {_platform_raw!r} is not a valid host or host:port. "
            "Use a host name or bracketed IPv6 address with an optional numeric "
            "port (1–65535), without a path.")

    SET_COMMANDS: bool = bool_env("SET_COMMANDS", True)

    DATABASE_URL: str = str_env("DATABASE_URL")

    if not DATABASE_URL:
        PROBLEMS.append("DATABASE_URL is not set")
    else:
        database_uri_problem = mongo_uri_problem(DATABASE_URL)
        if database_uri_problem:
            PROBLEMS.append(database_uri_problem)

    MAX_BATCH_FILES: int = int_env("MAX_BATCH_FILES", 50, minimum=1)

    CHANNEL: bool = bool_env("CHANNEL", False)

    BANNED_CHANNELS: Set[int] = int_set_env("BANNED_CHANNELS")

    MULTI_CLIENT: bool = False

    FORCE_CHANNEL_ID: Optional[int] = None
    FORCE_CHANNEL_PROBLEM: str = ""

    force_channel_env: str = str_env("FORCE_CHANNEL_ID")

    if force_channel_env:
        # Invalid optional config disables the feature with a warning.
        try:
            FORCE_CHANNEL_ID = int(force_channel_env)
        except ValueError:
            FORCE_CHANNEL_ID = None
        else:
            # Zero/positive IDs do not name a channel.
            if not FORCE_CHANNEL_ID or FORCE_CHANNEL_ID > 0:
                FORCE_CHANNEL_ID = None
        if FORCE_CHANNEL_ID is None:
            FORCE_CHANNEL_PROBLEM = (
                f"FORCE_CHANNEL_ID must be a channel id such as -1001234567890, "
                f"not {force_channel_env!r}. Force-subscribe is off until it is fixed.")

    TOKEN_ENABLED: bool = bool_env("TOKEN_ENABLED", False)
    TOKEN_TTL_HOURS: int = int_env("TOKEN_TTL_HOURS", 24, minimum=1)

    SHORTEN_ENABLED: bool = bool_env("SHORTEN_ENABLED", False)
    SHORTEN_MEDIA_LINKS: bool = bool_env("SHORTEN_MEDIA_LINKS", False)
    URL_SHORTENER_API_KEY: str = str_env("URL_SHORTENER_API_KEY")
    URL_SHORTENER_SITE: str = str_env("URL_SHORTENER_SITE")

    GLOBAL_RATE_LIMIT: bool = bool_env("GLOBAL_RATE_LIMIT", False)
    MAX_GLOBAL_REQUESTS_PER_MINUTE: int = int_env("MAX_GLOBAL_REQUESTS_PER_MINUTE", 4, minimum=1)

    RATE_LIMIT_ENABLED: bool = bool_env("RATE_LIMIT_ENABLED", False)
    MAX_FILES_PER_PERIOD: int = int_env("MAX_FILES_PER_PERIOD", 2, minimum=1)
    RATE_LIMIT_PERIOD_MINUTES: int = int_env("RATE_LIMIT_PERIOD_MINUTES", 1, minimum=1)
    MAX_QUEUE_SIZE: int = int_env("MAX_QUEUE_SIZE", 100, minimum=1)

    # Raised once, after every value has been looked at, so the report is complete.
    if PROBLEMS:
        logger.critical("%d configuration problem(s); see the startup report", len(PROBLEMS))
        raise ConfigError(PROBLEMS, CORRECTIONS)
