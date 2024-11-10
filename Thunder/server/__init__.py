# Thunder/server/__init__.py

from aiohttp import web
from aiohttp.web_exceptions import HTTPNotFound
from Thunder.server.stream_routes import routes
from Thunder.utils.logger import logger  # Use the custom logger


@web.middleware
async def custom_404_handler(request: web.Request, handler: web.RequestHandler) -> web.Response:
    """
    Middleware to handle 404 errors with a custom message.

    Args:
        request (web.Request): The incoming web request.
        handler (web.RequestHandler): The next request handler.

    Returns:
        web.Response: The HTTP response with a custom 404 message.
    """
    try:
        response = await handler(request)
        
        if isinstance(response, web.Response) and response.status == 404:
            # Return custom 404 page without logging it as an error
            return web.Response(
                text="<h1>Invalid link. Please check your URL.</h1>",
                content_type='text/html',
                status=404
            )
        
        return response
    except HTTPNotFound:
        # Handle HTTPNotFound exceptions explicitly without logging them as errors
        return web.Response(
            text="<h1>Invalid link. Please check your URL.</h1>",
            content_type='text/html',
            status=404
        )
    except Exception as e:
        # Log unexpected exceptions while handling requests
        logger.exception(f"Unhandled exception in middleware: {e}")
        return web.Response(
            text="<h1>An unexpected error occurred.</h1>",
            content_type='text/html',
            status=500
        )


async def web_server() -> web.Application:
    """
    Initializes the aiohttp web application with the necessary routes,
    custom middleware, and configures the maximum request body size.

    Returns:
        web.Application: The aiohttp web application instance.

    Raises:
        Exception: If initialization fails.
    """
    try:
        # Create web application with custom middleware and set max client request size to 30 MB
        web_app = web.Application(
            middlewares=[custom_404_handler],
            client_max_size=30 * 1024 * 1024  # 30 MB
        )
        web_app.add_routes(routes)
        
        return web_app
    except Exception as e:
        # Log any exceptions that occur during server initialization
        logger.exception(f"Failed to initialize web server: {e}")
        raise
