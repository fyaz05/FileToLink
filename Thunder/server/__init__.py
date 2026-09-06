# Thunder/server/__init__.py

import re
import time

from aiohttp import web

from Thunder.utils.logger import hash_path_token, logger

from .stream_routes import routes

# H10: access log middleware -- logs method, redacted path (file tokens are
# replaced by their sha256 prefix), status, bytes and duration.
# Modeled on ThunderGo's http/server.go logMiddleware + redactPath.


def _redact_path(path: str) -> str:
    # canonical: /f/<32-hex>/<name> or /watch/f/<32-hex>/<name>
    path = re.sub(
        r"(?<=/f/)[0-9a-f]{20,32}",
        lambda m: hash_path_token(m.group(0)),
        path,
    )
    # legacy: /watch/<6-char-hash><id>/<name> -> hash part
    path = re.sub(
        r"(?<=/watch/)[a-zA-Z0-9_-]{6}\d+",
        # hash the match itself -- the previous `m.group(0)[:-len(m.group(0))]`
        # slice always evaluated to "" so every file logged the same pseudonym
        lambda m: hash_path_token(m.group(0)) + "…",
        path,
    )
    # legacy id-first family: /<6-char-hash><id>/<name> -- the capability hash
    # is the path segment itself (previously logged in plaintext)
    path = re.sub(
        r"(?<=/)[a-zA-Z0-9_-]{6}\d+(?=/)",
        lambda m: hash_path_token(m.group(0)) + "…",
        path,
    )
    # activation tokens: /activate/<43-char urlsafe token>
    path = re.sub(
        r"(?<=/activate/)[A-Za-z0-9_-]{43}",
        lambda m: hash_path_token(m.group(0)),
        path,
    )
    return path


def _escape_control_chars(path: str) -> str:
    """Neutralize log forging: request.path is percent-DECODED, so a request
    for /f/x/%0A[INFO] fake would otherwise inject forged log lines."""
    return "".join(ch if ch.isprintable() else f"%{ord(ch):02X}" for ch in path)


@web.middleware
async def access_log_middleware(request: web.Request, handler):
    start = time.perf_counter()
    response: web.Response | None = None
    try:
        response = await handler(request)
    except web.HTTPException as e:
        response = e
        raise
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        try:
            # response stays None when the handler raised a non-HTTP
            # exception; getattr(None, ...) then falls back to 500
            status = getattr(response, "status", 500)
            size = getattr(response, "content_length", None)
            logger.info(
                f'{request.remote} "{request.method} '
                f'{_escape_control_chars(_redact_path(request.path))}" '
                f"{status} {size if size is not None else '-'} {duration_ms:.1f}ms"
            )
        except Exception:
            pass
    return response


async def web_server():
    # client_max_size removed (H4b): this is a GET-only server; the old 50 MiB
    # cap only governed request bodies that can never legitimately arrive.
    web_app = web.Application(middlewares=[access_log_middleware])
    web_app.add_routes(routes)
    return web_app
