"""Environment configuration, validated at import.

``config_values`` collects problems and raises one ``ConfigError`` so a single
restart reports everything that needs fixing.
"""

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
    normalise_host,
    str_env,
)
from Thunder.utils.logger import logger
from Thunder.utils.public_host import resolve_host

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
    FQDN: str = resolve_host(_fqdn_host, BIND_ADDRESS)
    FQDN_PORT: Optional[int] = _fqdn_port
    HAS_SSL: bool = bool_env("HAS_SSL", True)
    PROTOCOL: str = "https" if HAS_SSL else "http"
    # A port in FQDN wins: behind a proxy it is the public port, not the bound
    # one. Dropping it produced wrong-port (or portless) links, per NO_PORT.
    _public_port: Optional[int] = _fqdn_port or (None if NO_PORT else PORT)
    PORT_SEGMENT: str = f":{_public_port}" if _public_port else ""
    URL: str = f"{PROTOCOL}://{FQDN}{PORT_SEGMENT}/"

    if _fqdn_notes:
        CORRECTIONS.append(f"FQDN ({', '.join(_fqdn_notes)})")

    # normalise_host returns the whole string as host when the port is missing,
    # non-numeric or out of range (``a:b`` stays ``a:b``), so every link 404s;
    # internal whitespace is the same typo class.
    if _fqdn_host and not _fqdn_host.startswith("[") and (
            ":" in _fqdn_host or any(char.isspace() for char in _fqdn_host)):
        PROBLEMS.append(
            f"FQDN {_fqdn_host!r} is not a valid host: the port is missing, "
            "not numeric or out of range (1–65535), or the host has spaces. "
            "Write the host name, or host:port with a numeric port.")

    SET_COMMANDS: bool = bool_env("SET_COMMANDS", True)

    DATABASE_URL: str = str_env("DATABASE_URL")

    if not DATABASE_URL:
        PROBLEMS.append("DATABASE_URL is not set")

    MAX_BATCH_FILES: int = int_env("MAX_BATCH_FILES", 50, minimum=1)

    CHANNEL: bool = bool_env("CHANNEL", False)

    BANNED_CHANNELS: Set[int] = int_set_env("BANNED_CHANNELS")

    MULTI_CLIENT: bool = False

    FORCE_CHANNEL_ID: Optional[int] = None
    # Set only when the configured value could not be used; read by startup.
    FORCE_CHANNEL_PROBLEM: str = ""

    force_channel_env: str = str_env("FORCE_CHANNEL_ID")

    if force_channel_env:
        # Optional feature: an unusable value must not stop startup. Leave it
        # off and say so.
        try:
            FORCE_CHANNEL_ID = int(force_channel_env)
        except ValueError:
            FORCE_CHANNEL_ID = None
        else:
            # Channel ids are negative: 0 or positive parses but names no
            # channel, so force-subscribe would silently do something other
            # than configured.
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
