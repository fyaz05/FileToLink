# Thunder/server/__init__.py

import re
import time

from aiohttp import web

from Thunder.utils.logger import hash_path_token, logger

from .stream_routes import routes

# H10: access log middleware -- method, redacted path, status, bytes, duration.


# Single pass: sequential rules would let the id-first rule re-match 8-hex
# pseudonyms from the canonical rule (~39% end in two digits), double-hashing them.
# The (?=/|$) tail anchor also covers the no-filename legacy shape
# (/AbCdEf12345), whose bare hash+id is the whole link credential.
_PSEUDONYM_RE = re.compile(
    r"(?P<canon>(?<=/f/)[0-9a-f]{20,32})"
    r"|(?P<legacy>(?<=/watch/)[a-zA-Z0-9_-]{6}\d+)"
    r"|(?P<activate>(?<=/activate/)[A-Za-z0-9_-]{43})"
    r"|(?P<idfirst>(?<=/)[a-zA-Z0-9_-]{6}\d+(?=/|$))"
)

# "…" marks legacy segments whose id suffix was consumed by the hash;
# canonical and /activate/ tokens keep their bare pseudonym.
_TRUNCATED_GROUPS = frozenset({"legacy", "idfirst"})


def _pseudonymize(m: re.Match) -> str:
    token = m.group(0)
    suffix = "…" if m.lastgroup in _TRUNCATED_GROUPS else ""
    return hash_path_token(token) + suffix


def _redact_path(path: str) -> str:
    return _PSEUDONYM_RE.sub(_pseudonymize, path)


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
            # response is None for non-HTTP exceptions; getattr(None, ...) then yields 500
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
    # H4b: GET-only server -- no request bodies, so no client_max_size cap.
    web_app = web.Application(middlewares=[access_log_middleware])
    web_app.add_routes(routes)
    return web_app
