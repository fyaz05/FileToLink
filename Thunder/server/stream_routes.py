# Thunder/server/stream_routes.py

import re
import secrets
import time
from aiohttp import web
from urllib.parse import unquote, quote
from Thunder import StartTime, __version__
from Thunder.bot import multi_clients, StreamBot, work_loads
from Thunder.server.exceptions import FileNotFound, InvalidHash
from Thunder.utils.custom_dl import ByteStreamer
from Thunder.utils.logger import logger
from Thunder.utils.render_template import render_page
from Thunder.utils.time_format import get_readable_time

routes = web.RouteTableDef()

SECURE_HASH_LENGTH = 6
CHUNK_SIZE = 1024 * 1024
MAX_CONCURRENT_PER_CLIENT = 8
RANGE_REGEX = re.compile(r"bytes=(?P<start>\d*)-(?P<end>\d*)")
PATTERN_HASH_FIRST = re.compile(rf"^([a-zA-Z0-9_-]{{{SECURE_HASH_LENGTH}}})(\d+)(?:/.*)?$")
PATTERN_ID_FIRST = re.compile(r"^(\d+)(?:/.*)?$")
VALID_HASH_REGEX = re.compile(r'^[a-zA-Z0-9_-]+$')

streamers = {}

def get_streamer(client_id: int) -> ByteStreamer:
    """
    Retrieves a ByteStreamer instance for the specified client ID, creating and caching it if necessary.
    
    Args:
        client_id: The unique identifier of the client.
    
    Returns:
        The ByteStreamer instance associated with the given client ID.
    """
    if client_id not in streamers:
        streamers[client_id] = ByteStreamer(multi_clients[client_id])
    return streamers[client_id]

def parse_media_request(path: str, query: dict) -> tuple[int, str]:
    """
    Parses a media request path and query to extract the message ID and secure hash.
    
    Supports both hash-first and ID-first URL patterns. Validates that the secure hash is the correct length and contains only allowed characters. Raises InvalidHash if parsing or validation fails.
    
    Returns:
        A tuple containing the message ID and secure hash.
    """
    clean_path = unquote(path).strip('/')
    
    match = PATTERN_HASH_FIRST.match(clean_path)
    if match:
        try:
            message_id = int(match.group(2))
            secure_hash = match.group(1)
            if len(secure_hash) == SECURE_HASH_LENGTH and VALID_HASH_REGEX.match(secure_hash):
                return message_id, secure_hash
        except ValueError:
            pass
    
    match = PATTERN_ID_FIRST.match(clean_path)
    if match:
        try:
            message_id = int(match.group(1))
            secure_hash = query.get("hash", "").strip()
            if len(secure_hash) == SECURE_HASH_LENGTH and VALID_HASH_REGEX.match(secure_hash):
                return message_id, secure_hash
        except ValueError:
            pass
    
    raise InvalidHash("Invalid URL structure")

def select_optimal_client() -> tuple[int, ByteStreamer]:
    """
    Selects the client with the lowest workload under the concurrency limit.
    
    Returns:
        A tuple containing the selected client ID and its associated ByteStreamer instance.
    
    Raises:
        web.HTTPInternalServerError: If no clients are available.
    """
    if not work_loads:
        raise web.HTTPInternalServerError(text="No available clients")
    
    available_clients = [(cid, load) for cid, load in work_loads.items() if load < MAX_CONCURRENT_PER_CLIENT]
    
    if available_clients:
        client_id = min(available_clients, key=lambda x: x[1])[0]
    else:
        client_id = min(work_loads, key=work_loads.get)
    
    return client_id, get_streamer(client_id)

def parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    """
    Parses an HTTP Range header and returns the requested byte range.
    
    Args:
        range_header: The value of the HTTP Range header, or None for full content.
        file_size: The total size of the file in bytes.
    
    Returns:
        A tuple (start, end) representing the inclusive byte range to serve.
    
    Raises:
        web.HTTPBadRequest: If the Range header is malformed.
        web.HTTPRequestRangeNotSatisfiable: If the requested range is invalid or outside the file bounds.
    """
    if not range_header:
        return 0, file_size - 1
    
    match = RANGE_REGEX.match(range_header)
    if not match:
        raise web.HTTPBadRequest(text="Invalid range")
    
    start = int(match.group("start")) if match.group("start") else 0
    end = int(match.group("end")) if match.group("end") else file_size - 1
    
    if start < 0 or end >= file_size or start > end:
        raise web.HTTPRequestRangeNotSatisfiable(
            headers={"Content-Range": f"bytes */{file_size}"}
        )
    
    return start, end

@routes.get("/", allow_head=True)
async def root_redirect(request):
    """
    Redirects the root URL to the project's GitHub repository.
    """
    raise web.HTTPFound("https://github.com/fyaz05/FileToLink")

@routes.get("/status", allow_head=True)
async def status_endpoint(request):
    """
    Handles the /status endpoint, returning server and bot status as a JSON response.
    
    Provides operational status, version, uptime, bot username, active client count, and current workload distribution.
    """
    uptime = time.time() - StartTime
    total_load = sum(work_loads.values())
    
    workload_distribution = {str(k): v for k, v in sorted(work_loads.items())}
    
    return web.json_response({
        "server": {
            "status": "operational",
            "version": __version__,
            "uptime": get_readable_time(uptime)
        },
        "telegram_bot": {
            "username": f"@{StreamBot.username}",
            "active_clients": len(multi_clients)
        },
        "resources": {
            "total_workload": total_load,
            "workload_distribution": workload_distribution
        }
    })

@routes.get(r"/watch/{path:.+}", allow_head=True)
async def media_preview(request: web.Request):
    """
    Handles media preview requests by rendering an HTML page for the specified media.
    
    Parses the media request from the URL path and query parameters, then asynchronously renders a preview page. Returns a 404 response if the hash is invalid or the file is not found, and a 500 response for other server errors.
    """
    try:
        path = request.match_info["path"]
        message_id, secure_hash = parse_media_request(path, request.query)
        
        rendered_page = await render_page(message_id, secure_hash, requested_action='stream')
        return web.Response(text=rendered_page, content_type='text/html')
        
    except (InvalidHash, FileNotFound) as e:
        raise web.HTTPNotFound(text=str(e))
    except Exception as e:
        logger.error(f"Preview error: {e}")
        raise web.HTTPInternalServerError(text="Server error")

@routes.get(r"/{path:.+}", allow_head=True)
async def media_delivery(request: web.Request):
    """
    Handles HTTP requests for media file streaming with support for secure access and byte-range delivery.
    
    Parses the media request for message ID and secure hash, selects an optimal client for streaming, validates file existence and hash, and streams the requested byte range to the client. Supports HTTP Range requests for partial content and sets appropriate response headers. Returns 404 for invalid hashes or missing files, and 500 for internal server errors.
    """
    try:
        path = request.match_info["path"]
        message_id, secure_hash = parse_media_request(path, request.query)
        
        client_id, streamer = select_optimal_client()
        
        work_loads[client_id] += 1
        
        try:
            file_info = await streamer.get_file_info(message_id)
            if not file_info.get('unique_id'):
                raise FileNotFound("File not found")
            
            if file_info['unique_id'][:SECURE_HASH_LENGTH] != secure_hash:
                raise InvalidHash("Invalid hash")
            
            file_size = file_info.get('file_size', 0)
            if file_size == 0:
                raise FileNotFound("File size unavailable")
            
            range_header = request.headers.get("Range", "")
            start, end = parse_range_header(range_header, file_size)
            content_length = end - start + 1
            
            if start == 0 and end == file_size - 1:
                range_header = ""
            
            mime_type = file_info.get('mime_type') or 'application/octet-stream'
            filename = file_info.get('file_name') or f"file_{secrets.token_hex(4)}"
            
            headers = {
                "Content-Type": mime_type,
                "Content-Length": str(content_length),
                "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=31536000",
                "Connection": "keep-alive"
            }
            
            if range_header:
                headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            
            async def stream_generator():
                try:
                    bytes_sent = 0
                    bytes_to_skip = start % CHUNK_SIZE
                    
                    async for chunk in streamer.stream_file(message_id, offset=start, limit=content_length):
                        if bytes_to_skip > 0:
                            if len(chunk) <= bytes_to_skip:
                                bytes_to_skip -= len(chunk)
                                continue
                            else:
                                chunk = chunk[bytes_to_skip:]
                                bytes_to_skip = 0
                        
                        remaining = content_length - bytes_sent
                        if len(chunk) > remaining:
                            chunk = chunk[:remaining]
                        
                        if chunk:
                            yield chunk
                            bytes_sent += len(chunk)
                        
                        if bytes_sent >= content_length:
                            break
                finally:
                    work_loads[client_id] -= 1
            
            return web.Response(
                status=206 if range_header else 200,
                body=stream_generator(),
                headers=headers
            )
            
        except Exception:
            work_loads[client_id] -= 1
            raise
        
    except (InvalidHash, FileNotFound) as e:
        raise web.HTTPNotFound(text=str(e))
    except Exception as e:
        error_id = secrets.token_hex(6)
        logger.error(f"Server error {error_id}: {e}")
        raise web.HTTPInternalServerError(text=f"Server error (ID: {error_id})")
