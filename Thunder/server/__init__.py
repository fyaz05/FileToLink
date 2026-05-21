import logging

from aiohttp import web

from Thunder.utils.metrics import record_request
from Thunder.vars import Var

from .stream_routes import routes

_CORS_ORIGIN = getattr(Var, "FQDN", "*")
if not _CORS_ORIGIN or _CORS_ORIGIN == "0.0.0.0":
    _CORS_ORIGIN = "*"
    logging.warning("CORS origin is wildcard '*' — set FQDN env var for production security.")


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": _CORS_ORIGIN,
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "Range, Content-Type",
                "Access-Control-Max-Age": "86400",
            },
        )
    response = await handler(request)
    response.headers.setdefault("Access-Control-Allow-Origin", _CORS_ORIGIN)
    response.headers.setdefault("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
    response.headers.setdefault("Access-Control-Allow-Headers", "Range, Content-Type")
    response.headers.setdefault("Access-Control-Expose-Headers", "Content-Length, Content-Range, Content-Disposition")
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        response = await handler(request)
        record_request(request.path, response.status)
        return response
    except web.HTTPException as ex:
        record_request(request.path, ex.status)
        ex.headers["Cache-Control"] = "no-store"
        ex.headers.setdefault("Access-Control-Allow-Origin", _CORS_ORIGIN)
        raise
    except Exception:
        import secrets
        error_id = secrets.token_hex(6)
        logging.exception(f"Unhandled server error {error_id}")
        record_request(request.path, 500)
        return web.json_response(
            {"error": "Internal Server Error", "error_id": error_id},
            status=500,
            headers={
                "Cache-Control": "no-store",
                "Access-Control-Allow-Origin": _CORS_ORIGIN,
            },
        )


async def web_server():
    web_app = web.Application(
        client_max_size=50 * 1024 * 1024,
        middlewares=[error_middleware, cors_middleware],
    )
    web_app.add_routes(routes)
    return web_app
