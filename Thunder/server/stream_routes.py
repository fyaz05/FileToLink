import re
import time
import math
import logging
import secrets
import mimetypes
import asyncio
from functools import wraps
from typing import Tuple, Any

from aiohttp import web
from aiohttp.web_exceptions import HTTPNotFound
from aiohttp.http_exceptions import BadStatusLine
from aiohttp.client_exceptions import (
    ClientConnectionError,
    ClientPayloadError,
    ServerDisconnectedError,
)
from cachetools import LRUCache

from Thunder.bot import multi_clients, work_loads, StreamBot
from Thunder import StartTime, __version__
from ..utils.time_format import get_readable_time
from ..utils.custom_dl import ByteStreamer
from Thunder.utils.render_template import render_page
from Thunder.vars import Var
from Thunder.server.exceptions import FileNotFound, InvalidHash

import inspect  # For type checking

# Precompile regex patterns for efficiency
PATH_PATTERN_WITH_HASH = re.compile(r"^([a-zA-Z0-9_-]{6})(\d+)$")
PATH_PATTERN_WITH_ID = re.compile(r"(\d+)(?:/\S+)?")

# Define the routes for the web application
routes = web.RouteTableDef()

# Cache for ByteStreamer instances with a lock for thread safety
# The cache size is configurable via Var.CACHE_SIZE, defaulting to 100
class_cache: LRUCache = LRUCache(maxsize=int(getattr(Var, 'CACHE_SIZE', 100)))
class_cache_lock = asyncio.Lock()

def exception_handler(func):
    """
    Decorator to handle exceptions consistently across route handlers.

    Catches specific exceptions and raises appropriate HTTP errors.
    Avoids logging for standard HTTP exceptions like HTTPNotFound.
    """
    @wraps(func)
    async def wrapper(request):
        try:
            return await func(request)
        except InvalidHash:
            logging.warning(
                f"Invalid hash for path: {request.match_info.get('path', '')}"
            )
            raise web.HTTPForbidden(text="Invalid secure hash.")
        except FileNotFound as e:
            logging.warning(
                f"File not found for path: {request.match_info.get('path', '')}"
            )
            raise web.HTTPNotFound(text=str(e))
        except (
            AttributeError,
            BadStatusLine,
            ConnectionResetError,
            ClientConnectionError,
            ClientPayloadError,
            ServerDisconnectedError,
            asyncio.CancelledError,
        ):
            logging.warning("Client disconnected unexpectedly.")
            # 499 is a non-standard status code used by some servers to indicate client closed request
            return web.Response(status=499, text="Client Closed Request")
        except web.HTTPException:
            # Do not log standard HTTP exceptions like HTTPNotFound
            raise
        except Exception as e:
            logging.exception("Unhandled exception occurred.")
            raise web.HTTPInternalServerError(text="An unexpected error occurred.")
    return wrapper

def parse_path(request: web.Request, path_param: str) -> Tuple[int, str]:
    """
    Parses the path parameter to extract the message ID and secure hash.

    Args:
        request (web.Request): The incoming web request.
        path_param (str): The path parameter from the URL.

    Returns:
        tuple: A tuple containing the message ID (int) and secure hash (str).

    Raises:
        web.HTTPNotFound: If the path parameter is invalid or does not match the expected format.
    """
    logging.debug(f"Parsing path: {path_param}")

    # Try matching the path with a secure hash prefix
    match = PATH_PATTERN_WITH_HASH.match(path_param)
    if match:
        secure_hash = match.group(1)
        message_id = int(match.group(2))
        logging.debug(f"Extracted secure_hash: {secure_hash}, message_id: {message_id}")
    else:
        # Fallback: extract message_id and get secure_hash from query parameters
        id_match = PATH_PATTERN_WITH_ID.match(path_param)
        if id_match:
            message_id = int(id_match.group(1))
            secure_hash = request.rel_url.query.get("hash")
            if not secure_hash:
                # Secure hash is missing; raise 404 without logging an error
                raise web.HTTPNotFound(text="Invalid link. Secure hash is missing.")
            logging.debug(f"Extracted message_id: {message_id}, secure_hash from query: {secure_hash}")
        else:
            # Path parameter is invalid; raise 404 without logging an error
            raise web.HTTPNotFound(text="Invalid link. Please check your URL.")

    return message_id, secure_hash

def select_client() -> Tuple[int, Any]:
    """
    Selects the client with the minimal workload.

    Returns:
        Tuple[int, Any]: A tuple containing the client's index and the client object.
    """
    # Find the client with the least workload
    min_load_index = min(work_loads.items(), key=lambda x: x[1])[0]
    client = multi_clients[min_load_index]
    logging.debug(f"Selected client {min_load_index} with minimal load.")
    return min_load_index, client

# Routes

@routes.get("/status", allow_head=True)
async def root_route_handler(_):
    """
    Handles the '/status' endpoint to provide server status information.

    Returns:
        web.Response: JSON response containing server status details.
    """
    current_time = time.time()
    uptime = get_readable_time(current_time - StartTime)
    connected_bots = len(multi_clients)
    
    # Sort loads by bot index for consistent ordering
    loads = {
        f"bot{index}": load
        for index, load in sorted(work_loads.items(), key=lambda x: x[0])
    }

    response_data = {
        "server_status": "running",
        "uptime": uptime,
        "telegram_bot": f"@{StreamBot.username}",
        "connected_bots": connected_bots,
        "loads": loads,
        "version": __version__,
    }

    return web.json_response(response_data)

@routes.get(r"/watch/{path:\S+}", allow_head=True)
@exception_handler
async def stream_handler_watch(request: web.Request):
    """
    Handles the '/watch/{path}' endpoint to render the media player page.

    Args:
        request (web.Request): The incoming web request.

    Returns:
        web.Response: The HTTP response with the rendered HTML page.
    """
    path = request.match_info["path"]
    logging.debug(f"Handling watch request for path: {path}")
    message_id, secure_hash = parse_path(request, path)
    
    try:
        page_content = await render_page(message_id, secure_hash)
    except Exception as e:
        logging.warning(f"Error rendering page for message ID {message_id}: {e}")
        raise web.HTTPInternalServerError(text="Failed to render media page.")

    return web.Response(text=page_content, content_type='text/html')

@routes.get(r"/{path:\S+}", allow_head=True)
@exception_handler
async def stream_handler(request: web.Request):
    """
    Handles the '/{path}' endpoint to stream media content.

    Args:
        request (web.Request): The incoming web request.

    Returns:
        web.Response: The HTTP response with the media stream.
    """
    path = request.match_info["path"]
    logging.debug(f"Handling media stream request for path: {path}")
    message_id, secure_hash = parse_path(request, path)
    
    # Delegate to the media_streamer function to handle streaming
    return await media_streamer(request, message_id, secure_hash)

async def media_streamer(
    request: web.Request, message_id: int, secure_hash: str
) -> web.Response:
    """
    Streams media files to the client, handling range requests for efficient streaming.

    Args:
        request (web.Request): The incoming web request.
        message_id (int): The Telegram message ID of the media file.
        secure_hash (str): The secure hash to validate the request.

    Returns:
        web.Response: The HTTP response with the media stream.

    Raises:
        web.HTTPException: If any errors occur during processing.
    """
    range_header = request.headers.get("Range")
    logging.debug(f"Range header received: {range_header}")

    # Select the client with the minimal workload
    index, faster_client = select_client()
    if Var.MULTI_CLIENT:
        logging.info(f"Client {index} is now serving a request")

    # Thread-safe access to the class cache
    async with class_cache_lock:
        tg_connect = class_cache.get(faster_client)

    # If no cached ByteStreamer instance is found, create a new one
    if not tg_connect:
        try:
            tg_connect = ByteStreamer(faster_client)
            async with class_cache_lock:
                class_cache[faster_client] = tg_connect
            logging.debug(f"Created new ByteStreamer for client {index}")
        except Exception as e:
            logging.error(f"Failed to create ByteStreamer for client {index}: {e}")
            raise web.HTTPInternalServerError(text="Failed to initialize media stream.")

    # Retrieve file properties
    try:
        file_id = await tg_connect.get_file_properties(message_id)
        logging.debug(f"Retrieved file properties for message ID {message_id}: {file_id}")
    except InvalidHash:
        logging.warning(f"Invalid secure hash for message with ID {message_id}")
        raise web.HTTPForbidden(text="Invalid secure hash.")
    except FileNotFound as e:
        logging.warning(f"File not found for message ID {message_id}: {e}")
        raise web.HTTPNotFound(text="Requested file not found.")
    except Exception as e:
        logging.warning(f"Error retrieving file properties for message ID {message_id}: {e}")
        raise web.HTTPInternalServerError(text="Failed to retrieve file properties.")

    # Validate the secure hash
    if file_id.unique_id[:6] != secure_hash:
        logging.warning(f"Invalid secure hash for message with ID {message_id}")
        raise web.HTTPForbidden(text="Invalid secure hash.")

    file_size = file_id.file_size
    logging.debug(f"File size: {file_size}")

    # Handle Range header for partial content requests
    if range_header:
        range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if range_match:
            from_bytes = int(range_match.group(1))
            until_bytes = (
                int(range_match.group(2)) if range_match.group(2) else file_size - 1
            )
            logging.debug(f"Handling range from {from_bytes} to {until_bytes}")
        else:
            logging.warning(f"Invalid Range header format: {range_header}")
            raise web.HTTPBadRequest(text="Invalid Range header.")
    else:
        from_bytes = 0
        until_bytes = file_size - 1

    # Validate range values
    if (
        from_bytes >= file_size
        or until_bytes >= file_size
        or from_bytes < 0
        or until_bytes < from_bytes
    ):
        logging.warning(
            f"Requested Range Not Satisfiable: from_bytes={from_bytes}, until_bytes={until_bytes}, file_size={file_size}"
        )
        return web.Response(
            status=416,
            text="Requested Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    # Optimize chunk size for streaming
    default_chunk_size = 1 * 1024 * 1024  # 1 MB
    chunk_size = default_chunk_size

    # Calculate offsets and cuts for the requested range
    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = (until_bytes % chunk_size) + 1
    req_length = until_bytes - from_bytes + 1
    part_count = ((until_bytes - offset) // chunk_size) + 1

    logging.debug(
        f"Streaming parameters - offset: {offset}, first_part_cut: {first_part_cut}, "
        f"last_part_cut: {last_part_cut}, part_count: {part_count}, chunk_size: {chunk_size}"
    )

    # Determine MIME type and file name
    mime_type = file_id.mime_type or "application/octet-stream"
    file_name = (
        file_id.file_name
        or f"{secrets.token_hex(2)}{mimetypes.guess_extension(mime_type) or '.unknown'}"
    )

    # Set the appropriate headers and status code
    headers = {
        "Content-Type": mime_type,
        "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
        "Content-Length": str(req_length),
        "Content-Disposition": f'attachment; filename="{file_name}"',
        "Accept-Ranges": "bytes",
    }
    status = 206 if range_header else 200

    # Get the file stream generator
    try:
        body = tg_connect.yield_file(
            file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
        )
    except Exception as e:
        logging.warning(f"Error initiating file stream for message ID {message_id}: {e}")
        raise web.HTTPInternalServerError(text="Failed to initiate file stream.")

    # Determine the type of generator and wrap if necessary
    if inspect.isasyncgen(body):
        # It's an async generator, use it directly
        logging.debug("Using async generator for streaming.")
    elif inspect.isgenerator(body):
        # It's a synchronous generator, wrap it into an async generator
        logging.debug("Wrapping sync generator into async generator.")
        body = async_generator_from_sync(body)
    else:
        # If it's neither, raise an error
        logging.error("tg_connect.yield_file must return a generator.")
        raise TypeError("tg_connect.yield_file must return a generator.")

    # Return the streaming response with the appropriate body
    return web.Response(
        status=status,
        body=body,
        headers=headers,
    )

async def async_generator_from_sync(sync_gen):
    """
    Wraps a synchronous generator to make it asynchronous.

    Args:
        sync_gen (generator): The synchronous generator to wrap.

    Yields:
        bytes: The next chunk of data from the generator.
    """
    loop = asyncio.get_running_loop()
    iterator = iter(sync_gen)
    while True:
        try:
            # Run the next() call in a separate thread to avoid blocking the event loop
            chunk = await loop.run_in_executor(None, next, iterator)
            yield chunk
        except StopIteration:
            # Generator is exhausted
            break
        except Exception as e:
            # Log any unexpected exceptions and stop the generator
            logging.exception(f"Error in async generator: {e}")
            break
