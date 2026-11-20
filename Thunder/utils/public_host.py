"""Work out the public host the bot is reachable at.

``FQDN`` is optional; when empty the platform's host variable is used. The bind
address would produce unusable ``https://0.0.0.0/`` links.
"""

import os
from typing import Optional

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


def platform_host(environ=None) -> Optional[str]:
    """The platform's host for this deployment, if it publishes one.

    Returned as given (Render's is a URL, Heroku's a bare name); callers
    normalise it.
    """
    env = os.environ if environ is None else environ
    for name in PLATFORM_HOST_VARIABLES:
        value = (env.get(name) or "").strip()
        if not value:
            continue
        if name == "HEROKU_APP_NAME":
            return f"{value}{HEROKU_SUFFIX}"
        if name == "FLY_APP_NAME":
            return f"{value}{FLY_SUFFIX}"
        return value
    return None


def resolve_host(configured: str, bind_address: str, environ=None) -> str:
    """The host to build links from.

    A configured bind address loses to the platform's. The platform value is
    normalised too; returning it raw put ``https://https://host/`` in Render links.
    """
    if configured and not _is_bind_address(configured):
        return configured

    detected = platform_host(environ)
    if detected:
        host, _port, notes = normalise_host(detected)
        if notes:
            CORRECTIONS.append("platform host")
        return host

    # Nothing usable: keep the configured value so the warning can quote it and
    # the startup checks can report it.
    return configured or bind_address


def _is_bind_address(host: str) -> bool:
    return host in ("0.0.0.0", "127.0.0.1", "localhost", "::", "[::]", "::1", "[::1]")


def describe_missing_host(host: str) -> str:
    """One sentence naming the fix, used by the startup report."""
    if _is_bind_address(host):
        return (
            f"FQDN is unset or is the bind address ({host}), so links would "
            "point at the machine itself. Set FQDN to the address users reach "
            "this bot at, for example bot.example.com"
        )
    return ""


def describe_ignored_port(fqdn_port: Optional[int], port: int, no_port: bool) -> str:
    """Warn when FQDN's port disagrees with the bound one.

    A nudge only: behind a proxy the two may legitimately differ.
    """
    if fqdn_port is None or fqdn_port == port:
        return ""
    if no_port:
        return (f"FQDN has port {fqdn_port} and links use it; PORT is {port}. "
                f"That is fine behind a proxy, but remove the port from FQDN if "
                f"links should use no port.")
    return (f"FQDN has port {fqdn_port} and links use it; PORT is {port}. "
            f"Check that {fqdn_port} is the port users reach this bot on.")
