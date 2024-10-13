import re
import time
import math
import logging
import secrets
import mimetypes
import asyncio
from functools import wraps
from typing import Tuple

from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
from aiohttp.client_exceptions import ClientConnectionError, ClientPayloadError
from cachetools import LRUCache

from Thunder.bot import multi_clients, work_loads, StreamBot
from Thunder import StartTime, __version__
from ..utils.time_format import get_readable_time
from ..utils.custom_dl import ByteStreamer
from Thunder.utils.render_template import render_page
from Thunder.vars import Var
from Thunder.server.exceptions import FileNotFound, InvalidHash

# Define the routes for the web application
routes = web.RouteTableDef()

# Cache for ByteStreamer instances with a lock for thread safety
class_cache = LRUCache(maxsize=int(getattr(Var, 'CACHE_SIZE', 100)))
class_cache_lock = asyncio.Lock()

def exception_handler(func):
    """
    Decorator to handle exceptions consistently across route handlers.

    Catches specific exceptions and raises appropriate HTTP errors.
    """
    @wraps(func)
    async def wrapper(request):
        try:
            return await func(request)
        except InvalidHash as e:
            logging.warning(f"Invalid hash for path: {request.match_info.get('path', '')}")
            raise web.HTTPForbidden(text="Invalid secure hash.")
        except FileNotFound as e:
            logging.warning(f"File not found for path: {request.match_info.get('path', '')}")
            raise web.HTTPNotFound(text=e.message)
        except (AttributeError, BadStatusLine, ConnectionResetError, ClientConnectionError, ClientPayloadError) as e:
            logging.error(f"Client disconnected unexpectedly: {e}")
            raise web.HTTPInternalServerError(text="Client disconnected unexpectedly.")
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
        web.HTTPBadRequest: If the path parameter is invalid.
    """
    logging.debug(f"Parsing path: {path_param}")
    
    match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path_param)
    if match:
        secure_hash = match.group(1)
        message_id = int(match.group(2))
        logging.debug(f"Extracted secure_hash: {secure_hash}, message_id: {message_id}")
    else:
        id_match = re.search(r"(\d+)(?:/\S+)?", path_param)
        if id_match:
            message_id = int(id_match.group(1))
            secure_hash = request.rel_url.query.get("hash")
            logging.debug(f"Extracted message_id: {message_id}, secure_hash from query: {secure_hash}")
        else:
            logging.error(f"Invalid path parameter: {path_param}")
            raise web.HTTPBadRequest(text="Invalid path parameter.")
    return message_id, secure_hash

# Routes

@routes.get("/status", allow_head=True)
async def root_route_handler(_):
    """
    Handles the '/status' endpoint to provide server status information.

    This endpoint returns the current status of the server, including
    uptime, connected bots, bot load, and version information.
    """
    current_time = time.time()
    uptime = get_readable_time(current_time - StartTime)
    connected_bots = len(multi_clients)
    sorted_loads = sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
    loads = {f"bot{c + 1}": l for c, (_, l) in enumerate(sorted_loads)}

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
    page_content = await render_page(message_id, secure_hash)
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
    return await media_streamer(request, message_id, secure_hash)

async def media_streamer(request: web.Request, message_id: int, secure_hash: str):
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
    index = min(work_loads, key=work_loads.get)
    faster_client = multi_clients[index]
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
        except Exception as e:
            logging.error(f"Failed to create ByteStreamer for client {index}: {e}")
            raise web.HTTPInternalServerError(text="Failed to initialize media stream.")

    # Retrieve file properties
    file_id = await tg_connect.get_file_properties(message_id)
    logging.debug(f"Retrieved file properties for message ID {message_id}: {file_id}")

    # Validate the secure hash
    if file_id.unique_id[:6] != secure_hash:
        logging.error(f"Invalid secure hash for message with ID {message_id}")
        raise InvalidHash

    file_size = file_id.file_size
    logging.debug(f"File size: {file_size}")

    # Handle Range header for partial content requests
    if range_header:
        range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if range_match:
            from_bytes = int(range_match.group(1))
            until_bytes = int(range_match.group(2)) if range_match.group(2) else file_size - 1
            logging.debug(f"Handling range from {from_bytes} to {until_bytes}")
        else:
            logging.error(f"Invalid Range header: {range_header}")
            raise web.HTTPBadRequest(text="Invalid Range header.")
    else:
        from_bytes = 0
        until_bytes = file_size - 1

    # Validate range values
    if (until_bytes >= file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        logging.error(f"Requested Range Not Satisfiable: from_bytes={from_bytes}, until_bytes={until_bytes}, file_size={file_size}")
        return web.Response(
            status=416,
            text="Requested Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    # Optimize chunk size for streaming
    min_chunk_size = 128 * 1024  # 128 KB
    max_chunk_size = 8 * 1024 * 1024  # 8 MB
    default_chunk_size = 1 * 1024 * 1024  # 1 MB
    chunk_size = min(max(default_chunk_size, min_chunk_size), max_chunk_size)
    until_bytes = min(until_bytes, file_size - 1)

    # Calculate offsets and cuts for the requested range
    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1

    req_length = until_bytes - from_bytes + 1
    part_count = math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size)

    # Get the file stream generator
    body = tg_connect.yield_file(
        file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
    )

    # Determine MIME type and file name
    mime_type = file_id.mime_type or "application/octet-stream"
    file_name = file_id.file_name or f"{secrets.token_hex(2)}{mimetypes.guess_extension(mime_type) or '.unknown'}"

    # Set the appropriate headers and status code
    headers = {
        "Content-Type": mime_type,
        "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
        "Content-Length": str(req_length),
        "Content-Disposition": f'attachment; filename="{file_name}"',
        "Accept-Ranges": "bytes",
    }
    status = 206 if range_header else 200

    # Return the streaming response
    return web.Response(
        status=status,
        body=body,
        headers=headers,
    )