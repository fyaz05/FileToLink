# Thunder/server/__init__.py

from aiohttp import web


def __getattr__(name):
    # Lazy: a module-level import closes the file_properties -> server -> stream_routes cycle.
    if name == "routes":
        from .stream_routes import routes as _routes
        return _routes
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def web_server():
    from .stream_routes import routes

    web_app = web.Application(client_max_size=50 * 1024 * 1024)
    web_app.add_routes(routes)
    return web_app
