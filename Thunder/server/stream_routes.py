# Thunder/server/stream_routes.py

import asyncio
import inspect
import mimetypes
import re
import secrets
import time
import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionError
from cachetools import LRUCache
from functools import wraps
from urllib.parse import unquote, quote
from contextlib import asynccontextmanager

from Thunder import StartTime, __version__
from Thunder.bot import multi_clients, StreamBot, work_loads
from Thunder.server.exceptions import FileNotFound, InvalidHash
from Thunder.utils.custom_dl import ByteStreamer
from Thunder.utils.logger import logger
from Thunder.utils.render_template import render_page
from Thunder.utils.time_format import get_readable_time
from Thunder.vars import Var

routes = web.RouteTableDef()

# Configuration constants.
SECURE_HASH_LENGTH = 6
CHUNK_SIZE = 1 * 1024 * 1024
MAX_CLIENTS = 50
THREADPOOL_MAX_WORKERS = 10

# Updated regex: Correctly capture start and end values with named groups.
PATTERN_HASH_FIRST = re.compile(rf"^([a-zA-Z0-9_-]{{{SECURE_HASH_LENGTH}}})(\d+)(?:/.*)?$")
PATTERN_ID_FIRST = re.compile(r"^(\d+)(?:/.*)?$")
RANGE_REGEX = re.compile(r"bytes=(?P<start>\d*)-(?P<end>\d*)")

# Global LRU cache for ByteStreamer instances.
class_cache = LRUCache(maxsize=Var.CACHE_SIZE)
cache_lock = asyncio.Lock()

executor = ThreadPoolExecutor(max_workers=THREADPOOL_MAX_WORKERS)

def json_error(status, message):
    return json.dumps({"error": message})

def exception_handler(func):
    @wraps(func)
    async def wrapper(request):
        try:
            return await func(request)
        except InvalidHash as e:
            logger.debug(f"InvalidHash exception: {e}")
            raise web.HTTPForbidden(
                text=json_error(403, "Invalid security credentials"),
                content_type="application/json"
            )
        except FileNotFound as e:
            logger.debug(f"FileNotFound exception: {e}")
            raise web.HTTPNotFound(
                text=json_error(404, "File not found"),
                content_type="application/json"
            )
        except (ClientConnectionError, asyncio.CancelledError):
            return web.Response(status=499)
        except web.HTTPException:
            raise
        except Exception as e:
            error_id = secrets.token_hex(6)
            logger.error(f"Unhandled exception (ID: {error_id}): {str(e)}")
            logger.error(f"Stack trace for error {error_id}:\n{traceback.format_exc()}")
            
            raise web.HTTPInternalServerError(
                text=json_error(500, f"Internal server error (Reference ID: {error_id})"),
                content_type="application/json"
            )
    return wrapper

def parse_media_request(path: str, query: dict) -> tuple[int, str]:
    clean_path = unquote(path).strip('/')
    match = PATTERN_HASH_FIRST.match(clean_path)
    if match:
        try:
            message_id = int(match.group(2))
            secure_hash = match.group(1)
            if not re.match(r'^[a-zA-Z0-9_-]+$', secure_hash):
                raise InvalidHash("Security token contains invalid characters")
            return message_id, secure_hash
        except ValueError:
            raise InvalidHash("Invalid message ID format")
    match = PATTERN_ID_FIRST.match(clean_path)
    if match:
        try:
            message_id = int(match.group(1))
            secure_hash = query.get("hash", "").strip()
            if len(secure_hash) != SECURE_HASH_LENGTH:
                raise InvalidHash("Security token length mismatch")
            if not re.match(r'^[a-zA-Z0-9_-]+$', secure_hash):
                raise InvalidHash("Security token contains invalid characters")
            return message_id, secure_hash
        except ValueError:
            raise InvalidHash("Invalid message ID format")
    raise InvalidHash("Invalid URL structure")

def optimal_client_selection():
    if not work_loads:
        raise web.HTTPInternalServerError(
            text=json_error(500, "No available clients"),
            content_type="application/json"
        )
    client_id, _ = min(work_loads.items(), key=lambda item: item[1])
    return client_id, multi_clients[client_id]

async def get_cached_streamer(client) -> ByteStreamer:
    streamer = class_cache.get(client)
    if streamer is None:
        async with cache_lock:
            streamer = class_cache.get(client)
            if streamer is None:
                streamer = ByteStreamer(client)
                class_cache[client] = streamer
    return streamer

def sanitize_filename(filename: str) -> str:
    return re.sub(r'[\n\r";]', '', filename)

@asynccontextmanager
async def track_workload(client_id):
    work_loads[client_id] += 1
    try:
        yield
    finally:
        work_loads[client_id] -= 1

@routes.get("/", allow_head=True)
@exception_handler
async def root_redirect(request):
    raise web.HTTPFound("https://github.com/fyaz05/FileToLink")

@routes.get("/status", allow_head=True)
async def status_endpoint(request):
    uptime_seconds = time.time() - StartTime
    uptime_formatted = get_readable_time(uptime_seconds)
    
    sorted_workloads = dict(sorted(
        {f"client_{k}": v for k, v in work_loads.items()}.items(),
        key=lambda item: item[1]
    ))
    
    total_load = sum(work_loads.values())
    if total_load < MAX_CLIENTS * 0.5:
        status_level = "optimal"
    elif total_load < MAX_CLIENTS * 0.8:
        status_level = "high"
    else:
        status_level = "critical"
    
    status_data = {
        "server": {
            "status": "operational",
            "status_level": status_level,
            "version": __version__,
            "uptime": uptime_formatted,
            "uptime_seconds": int(uptime_seconds),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        },
        "telegram_bot": {
            "username": f"@{StreamBot.username}",
            "active_clients": len(multi_clients)
        },
        "resources": {
            "total_workload": total_load,
            "max_clients": MAX_CLIENTS,
            "workload_distribution": sorted_workloads,
            "cache": {
                "current": len(class_cache),
                "maximum": Var.CACHE_SIZE,
                "utilization_percent": round((len(class_cache) / Var.CACHE_SIZE) * 100, 2)
            }
        },
        "system": {
            "chunk_size": f"{CHUNK_SIZE / (1024 * 1024):.1f} MB",
            "thread_pool_workers": THREADPOOL_MAX_WORKERS
        }
    }
    
    return web.json_response(
        status_data,
        dumps=lambda obj: json.dumps(obj, indent=2, sort_keys=False)
    )

@routes.get(r"/watch/{path:.+}", allow_head=True)
@exception_handler
async def media_preview(request: web.Request):
    path = request.match_info["path"]
    message_id, secure_hash = parse_media_request(path, request.query)
    rendered_page = await render_page(message_id, secure_hash, requested_action='stream')
    return web.Response(
        text=rendered_page,
        content_type='text/html',
        headers={
            "Cache-Control": "no-cache, must-revalidate",
            "Content-Security-Policy": "default-src 'self' https:; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://cdn.plyr.io; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://cdn.plyr.io; img-src 'self' data: https:; font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; connect-src 'self' https:; media-src 'self' https:;",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN",  
            "X-XSS-Protection": "1; mode=block",
            "Referrer-Policy": "strict-origin-when-cross-origin"
        }
    )

@routes.get(r"/{path:.+}", allow_head=True)
@exception_handler
async def media_delivery(request: web.Request):
    client_id, client = optimal_client_selection()
    async with track_workload(client_id):
        path = request.match_info["path"]
        message_id, secure_hash = parse_media_request(path, request.query)
        return await handle_media_stream(request, message_id, secure_hash, client_id, client)

async def handle_media_stream(request, message_id, secure_hash, client_id, client):
    streamer = await get_cached_streamer(client)
    file_meta = await streamer.get_file_properties(message_id)
    if file_meta.unique_id[:SECURE_HASH_LENGTH] != secure_hash:
        raise InvalidHash("Security token mismatch")
    file_size = file_meta.file_size
    range_header = request.headers.get("Range", "")
    if range_header:
        range_match = RANGE_REGEX.fullmatch(range_header)
        if not range_match:
            logger.debug(f"Malformed range header received: {range_header}")
            raise web.HTTPBadRequest(
                text=json_error(400, "Malformed range header"),
                content_type="application/json"
            )
        start = int(range_match.group("start")) if range_match.group("start") else 0
        end = int(range_match.group("end")) if range_match.group("end") else file_size - 1
    else:
        try:
            if hasattr(request, "http_range") and request.http_range:
                start = request.http_range.start or 0
                end = (request.http_range.stop or file_size) - 1
            else:
                start, end = 0, file_size - 1
        except Exception:
            start, end = 0, file_size - 1
    if start < 0 or end >= file_size or start > end:
        logger.debug(f"Invalid range request: start={start}, end={end}, file_size={file_size}")
        raise web.HTTPRequestRangeNotSatisfiable(
            headers={"Content-Range": f"bytes */{file_size}"}
        )
    offset = start - (start % CHUNK_SIZE)
    first_chunk_cut = start - offset
    last_chunk_cut = (end % CHUNK_SIZE) + 1
    total_chunks = ((end - offset) // CHUNK_SIZE) + 1
    mime_type = file_meta.mime_type or mimetypes.guess_type(file_meta.file_name)[0] or "application/octet-stream"
    disposition = "inline" if mime_type.startswith(("video/", "audio/")) else "attachment"
    original_filename = unquote(file_meta.file_name) if file_meta.file_name else f"file_{secrets.token_hex(4)}"
    safe_filename = sanitize_filename(original_filename)
    encoded_filename = quote(safe_filename)
    headers = {
        "Content-Type": mime_type,
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded_filename}",
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=31536000, immutable",
        "Connection": "keep-alive",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin"
    }
    if hasattr(streamer, 'async_yield_file'):
        stream_generator = streamer.async_yield_file(
            file_meta, client_id, offset, first_chunk_cut, last_chunk_cut, total_chunks, CHUNK_SIZE
        )
    else:
        stream_generator = streamer.yield_file(
            file_meta, client_id, offset, first_chunk_cut, last_chunk_cut, total_chunks, CHUNK_SIZE
        )
    if inspect.isgenerator(stream_generator):
        stream_generator = async_gen_wrapper(stream_generator)
    return web.Response(
        status=206 if range_header else 200,
        body=stream_generator,
        headers=headers
    )

async def async_gen_wrapper(sync_gen):
    loop = asyncio.get_running_loop()
    try:
        while True:
            try:
                yield await loop.run_in_executor(executor, next, sync_gen)
            except StopIteration:
                break
    except Exception as e:
        logger.error(f"Error in async generator wrapper: {e}")
        try:
            sync_gen.close()
        except Exception:
            pass