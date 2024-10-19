from aiohttp import web
from aiohttp.web_exceptions import HTTPNotFound
from .stream_routes import routes

import logging

# Define the custom 404 error handler middleware
@web.middleware
async def custom_404_handler(request, handler):
    """
    Middleware to handle 404 errors with a custom message.

    Args:
        request (web.Request): The incoming web request.
        handler (callable): The next request handler.

    Returns:
        web.Response: The HTTP response with a custom 404 message.
    """
    try:
        response = await handler(request)
        if response.status == 404:
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

async def web_server():
    """
    Initializes the aiohttp web application with the necessary routes,
    custom middleware, and configures the maximum request body size.

    Returns:
        web.Application: The aiohttp web application instance.
    """
    # Create web application with custom middleware and set max client request size to 30 MB
    web_app = web.Application(middlewares=[custom_404_handler], client_max_size=30 * 1024 * 1024)
    web_app.add_routes(routes)
    return web_app
