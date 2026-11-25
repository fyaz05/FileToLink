"""Resolve the public host and port from FQDN, platform variables, or bind address."""

import os
from typing import Optional, Tuple

from Thunder.utils.config_values import CORRECTIONS, normalise_host

# Ordered: first set name wins. Each platform injects one with its host.
PLATFORM_HOST_VARIABLES = (
    "KOYEB_PUBLIC_DOMAIN",
    "RENDER_EXTERNAL_URL",
    "RAILWAY_PUBLIC_DOMAIN",
    "HEROKU_APP_NAME",
    "FLY_APP_NAME",
    "WEBSITE_HOSTNAME",  # Azure App Service
)

HEROKU_SUFFIX = ".herokuapp.com"
FLY_SUFFIX = ".fly.dev"


def platform_host_source(environ=None) -> Tuple[Optional[str], Optional[str]]:
    """Return the selected platform variable name and its host value."""
    env = os.environ if environ is None else environ
    for name in PLATFORM_HOST_VARIABLES:
        value = (env.get(name) or "").strip()
        if not value:
            continue
        if name == "HEROKU_APP_NAME":
            return name, f"{value}{HEROKU_SUFFIX}"
        if name == "FLY_APP_NAME":
            return name, f"{value}{FLY_SUFFIX}"
        return name, value
    return None, None


def platform_host(environ=None) -> Optional[str]:
    """Return the platform host or URL for normalization."""
    return platform_host_source(environ)[1]


def resolve_public_host(configured: str, configured_port: Optional[int],
                        bind_address: str, environ=None) -> Tuple[str, Optional[int]]:
    """Resolve host and port together; discard ports from overridden bind addresses."""
    if configured and not _is_bind_address(configured):
        return configured, configured_port

    detected = platform_host(environ)
    if detected:
        host, port, notes = normalise_host(detected)
        if host:
            if notes:
                CORRECTIONS.append("platform host")
            return host, port

    # Nothing usable: keep the configured value so the warning can quote it and
    # the startup checks can report it.
    return configured or bind_address, configured_port


def resolve_host(configured: str, bind_address: str, environ=None) -> str:
    return resolve_public_host(configured, None, bind_address, environ)[0]


def is_bind_address(host: str) -> bool:
    return host in ("0.0.0.0", "127.0.0.1", "localhost", "::", "[::]", "::1", "[::1]")


def _is_bind_address(host: str) -> bool:
    return is_bind_address(host)


def describe_missing_host(host: str) -> str:
    if _is_bind_address(host):
        return (
            f"FQDN is unset or is the bind address ({host}), so links would "
            "point at the machine itself. Set FQDN to the address users reach "
            "this bot at, for example bot.example.com"
        )
    return ""


def describe_ignored_port(fqdn_port: Optional[int], port: int, no_port: bool) -> str:
    if fqdn_port is None or fqdn_port == port:
        return ""
    if no_port:
        return (f"FQDN has port {fqdn_port} and links use it; PORT is {port}. "
                f"That is fine behind a proxy, but remove the port from FQDN if "
                f"links should use no port.")
    return (f"FQDN has port {fqdn_port} and links use it; PORT is {port}. "
            f"Check that {fqdn_port} is the port users reach this bot on.")
