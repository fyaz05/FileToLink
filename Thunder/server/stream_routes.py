# Thunder/server/stream_routes.py

import asyncio
import inspect
import mimetypes
import re
import secrets
import time
from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionError
from cachetools import LRUCache
from functools import wraps
from urllib.parse import unquote, quote

from Thunder import StartTime, __version__
from Thunder.bot import multi_clients, StreamBot, work_loads
from Thunder.server.exceptions import FileNotFound, InvalidHash
from Thunder.utils.custom_dl import ByteStreamer
from Thunder.utils.logger import logger
from Thunder.utils.render_template import render_page
from Thunder.utils.time_format import get_readable_time
from Thunder.vars import Var

routes = web.RouteTableDef()
SECURE_HASH_LENGTH = 6  # Ensure this matches your security configuration
PATH_REGEX = re.compile(
    rf"^(?:(\d+)|([a-zA-Z0-9]{{{SECURE_HASH_LENGTH}}})(\d+))(?:/.*)?$"
)
RANGE_REGEX = re.compile(r"bytes=(?P<start>\d*)-(?P<end>\d*)")

# Cache configuration
class_cache = LRUCache(maxsize=Var.CACHE_SIZE)
cache_lock = asyncio.Lock()

def exception_handler(func):
    @wraps(func)
    async def wrapper(request):
        try:
            return await func(request)
        except InvalidHash as e:
            logger.warning(f"Security violation: {str(e)}")
            raise web.HTTPForbidden(text="Invalid security credentials")
        except FileNotFound as e:
            logger.warning(f"Resource not found: {str(e)}")
            raise web.HTTPNotFound(text=str(e))
        except (ClientConnectionError, asyncio.CancelledError):
            logger.debug("Client connection terminated prematurely")
            return web.Response(status=499)  # Client Closed Request
        except web.HTTPException:
            raise
        except Exception as e:
            logger.critical(f"Critical failure: {str(e)}", exc_info=True)
            raise web.HTTPInternalServerError(text="Internal server error")
    return wrapper

def parse_media_request(path: str, query: dict) -> tuple[int, str]:
    """Robust parser for both hash-first and ID-first URL patterns"""
    clean_path = unquote(path).strip('/')
    match = PATH_REGEX.match(clean_path)
    
    if not match:
        raise InvalidHash("Invalid URL structure")

    if match.group(2):  # Hash-first format
        return int(match.group(3)), match.group(2)
    
    # ID-first format
    message_id = int(match.group(1))
    secure_hash = query.get("hash", "")
    
    if len(secure_hash) != SECURE_HASH_LENGTH:
        raise InvalidHash("Security token length mismatch")
    
    return message_id, secure_hash

def optimal_client_selection():
    """Select client with least load using efficient min-search"""
    min_load, client_id = min((v, k) for k, v in work_loads.items())
    return client_id, multi_clients[client_id]

async def get_cached_streamer(client) -> ByteStreamer:
    """Thread-safe cached streamer acquisition"""
    async with cache_lock:
        if client not in class_cache:
            class_cache[client] = ByteStreamer(client)
            logger.debug(f"Created new streamer for client {id(client)}")
        return class_cache[client]

@routes.get("/status", allow_head=True)
async def status_endpoint(_):
    """Comprehensive system status endpoint"""
    return web.json_response({
        "status": "operational",
        "uptime": get_readable_time(time.time() - StartTime),
        "bot_username": f"@{StreamBot.username}",
        "active_clients": len(multi_clients),
        "workload_distribution": {f"client_{k}": v for k, v in work_loads.items()},
        "version": __version__,
        "cache_utilization": f"{len(class_cache)}/{Var.CACHE_SIZE}"
    })

@routes.get(r"/watch/{path:.+}", allow_head=True)
@exception_handler
async def media_preview(request: web.Request):
    """Rich media preview endpoint with security validation"""
    path = request.match_info["path"]
    message_id, secure_hash = parse_media_request(path, request.query)
    
    try:
        return web.Response(
            text=await render_page(message_id, secure_hash),
            content_type='text/html',
            headers={"Cache-Control": "no-cache, must-revalidate"}
        )
    except Exception as e:
        logger.error(f"Preview generation failed: {str(e)}")
        raise web.HTTPInternalServerError(text="Preview unavailable")

@routes.get(r"/{path:.+}", allow_head=True)
@exception_handler
async def media_delivery(request: web.Request):
    """High-performance media streaming endpoint"""
    path = request.match_info["path"]
    message_id, secure_hash = parse_media_request(path, request.query)
    return await handle_media_stream(request, message_id, secure_hash)

async def handle_media_stream(request, message_id, secure_hash):
    """Core media streaming logic with browser compatibility improvements"""
    client_id, client = optimal_client_selection()
    streamer = await get_cached_streamer(client)

    # Security validation
    file_meta = await streamer.get_file_properties(message_id)
    if file_meta.unique_id[:SECURE_HASH_LENGTH] != secure_hash:
        raise InvalidHash("Security token mismatch")

    # Range header processing
    range_header = request.headers.get("Range", "")
    file_size = file_meta.file_size
    start, end = 0, file_size - 1

    if range_header:
        range_match = RANGE_REGEX.fullmatch(range_header)
        if not range_match:
            raise web.HTTPBadRequest(text="Malformed range header")

        start_str = range_match.group("start")
        end_str = range_match.group("end")
        
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1

        # Validate range bounds
        if any([start >= file_size, end >= file_size, start > end]):
            raise web.HTTPRequestRangeNotSatisfiable(
                headers={"Content-Range": f"bytes */{file_size}"}
            )

    # Stream configuration
    chunk_size = 1 * 1024 * 1024  # 1MB chunks
    offset = start - (start % chunk_size)
    first_chunk_cut = start - offset
    last_chunk_cut = (end % chunk_size) + 1
    total_chunks = ((end - offset) // chunk_size) + 1

    # Content headers setup
    mime_type = (
        file_meta.mime_type or
        mimetypes.guess_type(file_meta.file_name)[0] or
        "application/octet-stream"
    )
    disposition = "inline" if mime_type.startswith(("video/", "audio/")) else "attachment"

    # --- Modification Starts Here ---

    # Use the original filename or generate a default one
    original_filename = (
        unquote(file_meta.file_name) if file_meta.file_name
        else f"file_{secrets.token_hex(4)}"
    )

    # Clean the filename to remove problematic characters
    safe_filename = original_filename.replace('\n', '').replace('\r', '').replace('"', '').replace(';', '')

    # Percent-encode the filename for the filename* parameter
    encoded_filename = quote(safe_filename)

    # Set the Content-Disposition header
    headers = {
        "Content-Type": mime_type,
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(end - start + 1),
        # Use only the filename* parameter for compatibility
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded_filename}",
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=31536000, immutable"
    }

    # --- Modification Ends Here ---

    # Generator adaptation
    stream_generator = streamer.yield_file(
        file_meta, client_id, offset,
        first_chunk_cut, last_chunk_cut,
        total_chunks, chunk_size
    )

    if inspect.isgenerator(stream_generator):
        stream_generator = async_gen_wrapper(stream_generator)

    return web.Response(
        status=206 if range_header else 200,
        body=stream_generator,
        headers=headers
    )

async def async_gen_wrapper(sync_gen):
    """Convert synchronous generator to asynchronous"""
    loop = asyncio.get_event_loop()
    while True:
        try:
            yield await loop.run_in_executor(None, next, sync_gen)
        except StopIteration:
            break
