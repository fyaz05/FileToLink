class InvalidHash(Exception):
    pass


class FileNotFound(Exception):
    """The file/record is genuinely gone.  Safe to self-heal a record on."""


class TelegramUnavailable(Exception):
    """Transient Telegram-side failure (FloodWait-exhaustion, timeout,
    transport error).  MUST NOT trigger record self-healing -- raising it
    as FileNotFound made a Telegram brownout delete every vault record
    requested during the outage.  Route handlers map this to 503."""


# The shared HTTP error contract lives here because this module is the bottom
# of the server package: it imports nothing, so both stream_routes and
# render_template can depend on it without a cycle.  render_template has to
# import it lazily (importing any Thunder.server submodule runs the package
# __init__, which imports stream_routes, which imports render_template).

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "Range, Content-Type",
    "Access-Control-Expose-Headers": "Content-Length, Content-Range, Content-Disposition",
}

# Every error response carries CORS (readable browser errors) + no-store
# (a cached failure must never outlive the underlying problem).
ERROR_HEADERS = {**CORS_HEADERS, "Cache-Control": "no-store"}
RETRY_5 = {**ERROR_HEADERS, "Retry-After": "5"}
RETRY_2 = {**ERROR_HEADERS, "Retry-After": "2"}


def unavailable(text: str, *, retry_after: str = "5"):
    """503 + Retry-After + no-store, from one place.

    Every 5xx the server emits must carry no-store, so a CDN or a browser does
    not cache a transient failure. Hand-rolling these headers per call site is
    how one of them ended up missing it.
    """
    from aiohttp import web

    headers = {"Retry-After": retry_after}
    return web.HTTPServiceUnavailable(text=text, headers={**ERROR_HEADERS, **headers})
