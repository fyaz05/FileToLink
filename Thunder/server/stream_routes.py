from __future__ import annotations

import re
import secrets
import time
from urllib.parse import quote, unquote

from aiohttp import web
from pytdbot import types

from Thunder import StartTime, __version__
from Thunder.bot import StreamBot, multi_clients, work_loads
from Thunder.server.exceptions import FileNotFound, InvalidHash, RateLimited
from Thunder.utils.bot_utils import quote_media_name
from Thunder.utils.canonical_files import (
    PUBLIC_HASH_LENGTH,
    get_file_by_hash,
    update_cached_file_id,
)
from Thunder.utils.compat import _get_media_file
from Thunder.utils.custom_dl import ByteStreamer
from Thunder.utils.logger import logger
from Thunder.utils.media_helpers import _get_extension_for_content_type
from Thunder.utils.metrics import get_metrics_text
from Thunder.utils.render_template import render_media_page, render_page
from Thunder.utils.time_format import get_readable_time
from Thunder.vars import Var

routes = web.RouteTableDef()

SECURE_HASH_LENGTH = 6
CHUNK_SIZE = 1024 * 1024
MAX_CONCURRENT_PER_CLIENT = 8
RANGE_REGEX = re.compile(r"^bytes=(?P<start>\d*)-(?P<end>\d*)$")
PATTERN_HASH_FIRST = re.compile(
    rf"^([a-zA-Z0-9_-]{{{SECURE_HASH_LENGTH}}})(\d+)(?:/.*)?$")
PATTERN_ID_FIRST = re.compile(r"^(\d+)(?:/.*)?$")
VALID_HASH_REGEX = re.compile(r'^[a-zA-Z0-9_-]+$')
VALID_PUBLIC_HASH_REGEX = re.compile(rf'^[0-9a-f]{{{PUBLIC_HASH_LENGTH}}}$')
VALID_DISPOSITIONS = {"inline", "attachment"}

streamers = {}
_cached_bot_username: str | None = None


def get_streamer(client_id: int) -> ByteStreamer:
    if client_id not in streamers:
        streamers[client_id] = ByteStreamer(multi_clients[client_id])
    return streamers[client_id]


def parse_media_request(path: str, query: dict) -> tuple[int, str]:
    clean_path = unquote(path).strip('/')

    match = PATTERN_HASH_FIRST.match(clean_path)
    if match:
        try:
            message_id = int(match.group(2))
            secure_hash = match.group(1)
            if (len(secure_hash) == SECURE_HASH_LENGTH and
                    VALID_HASH_REGEX.match(secure_hash)):
                return message_id, secure_hash
        except ValueError as e:
            raise InvalidHash(f"Invalid message ID format in path: {e}") from e

    match = PATTERN_ID_FIRST.match(clean_path)
    if match:
        try:
            message_id = int(match.group(1))
            secure_hash = query.get("hash", "").strip()
            if (len(secure_hash) == SECURE_HASH_LENGTH and
                    VALID_HASH_REGEX.match(secure_hash)):
                return message_id, secure_hash
            else:
                raise InvalidHash("Invalid or missing hash in query parameter")
        except ValueError as e:
            raise InvalidHash(f"Invalid message ID format in path: {e}") from e

    raise InvalidHash("Invalid URL structure or missing hash")


def validate_public_hash(public_hash: str) -> str:
    secure_hash = public_hash.strip().lower()
    if len(secure_hash) != PUBLIC_HASH_LENGTH or not VALID_PUBLIC_HASH_REGEX.match(secure_hash):
        raise InvalidHash("Invalid canonical file hash")
    return secure_hash


def select_optimal_client() -> tuple[int, ByteStreamer]:
    if not work_loads:
        raise web.HTTPInternalServerError(
            text="No available clients to handle the request. Please try again later.")

    available_clients = [
        (cid, load) for cid, load in work_loads.items()
        if load < MAX_CONCURRENT_PER_CLIENT]

    if available_clients:
        client_id = min(available_clients, key=lambda x: x[1])[0]
        return client_id, get_streamer(client_id)

    # Graceful degradation: pick least-loaded even at capacity
    client_id = min(work_loads.items(), key=lambda x: x[1])[0]
    return client_id, get_streamer(client_id)


def get_content_disposition(request: web.Request) -> str:
    disposition = request.query.get("disposition", "attachment").strip().lower()
    return disposition if disposition in VALID_DISPOSITIONS else "attachment"


def parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    if not range_header:
        return 0, file_size - 1

    match = RANGE_REGEX.fullmatch(range_header)
    if not match:
        raise web.HTTPBadRequest(text=f"Invalid range header: {range_header}")

    start_str = match.group("start")
    end_str = match.group("end")
    if start_str:
        start = int(start_str)
        end = int(end_str) if end_str else file_size - 1
    else:
        if not end_str:
            raise web.HTTPBadRequest(text=f"Invalid range header: {range_header}")
        suffix_len = int(end_str)
        if suffix_len <= 0:
            raise web.HTTPRequestRangeNotSatisfiable(
                headers={"Content-Range": f"bytes */{file_size}"})
        start = max(file_size - suffix_len, 0)
        end = file_size - 1

    if start < 0 or end >= file_size or start > end:
        raise web.HTTPRequestRangeNotSatisfiable(
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    return start, end


def _resolve_unique_id(file_info: dict) -> str:
    unique_id = file_info.get("unique_id") or file_info.get("file_unique_id")
    if not unique_id:
        raise FileNotFound("File unique ID not found in info.")
    return unique_id


def _resolve_filename(file_info: dict) -> str:
    filename = file_info.get("file_name")
    if filename:
        return filename

    content_type = file_info.get("media_type", "")
    ext = _get_extension_for_content_type(content_type).lstrip(".")
    return f"file_{secrets.token_hex(4)}.{ext}"


async def _get_cached_bot_username() -> str:
    global _cached_bot_username
    if _cached_bot_username:
        return _cached_bot_username
    me = await StreamBot.getMe()
    if isinstance(me, types.Error):
        return "unknown"
    if hasattr(me, "usernames") and me.usernames:
        _cached_bot_username = me.usernames.editable_username or "unknown"
    else:
        _cached_bot_username = getattr(me, "username", "unknown")
    return _cached_bot_username


def _require_admin_token(request: web.Request) -> None:
    admin_token = getattr(Var, "ADMIN_TOKEN", "")
    if not admin_token:
        return
    auth_header = request.headers.get("Authorization", "")
    if auth_header != f"Bearer {admin_token}":
        raise web.HTTPUnauthorized(text="Missing or invalid authorization token.")


async def _serve_media_response(
    request: web.Request,
    *,
    file_info: dict,
    streamer: ByteStreamer,
    client_id: int,
    media_ref: int | str,
    fallback_message_id: int | None = None,
    on_fallback_message=None,
    extra_headers: dict | None = None,
):
    file_size = int(file_info.get('file_size', 0) or 0)
    if file_size == 0:
        raise FileNotFound("File size is reported as zero or unavailable.")

    range_header = request.headers.get("Range", "")
    start, end = parse_range_header(range_header, file_size)
    content_length = end - start + 1

    if start == 0 and end == file_size - 1:
        range_header = ""

    mime_type = file_info.get('mime_type') or 'application/octet-stream'
    filename = _resolve_filename(file_info)
    disposition = get_content_disposition(request)

    headers = {
        "Content-Type": mime_type,
        "Content-Length": str(content_length),
        "Content-Disposition": (
            f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}"),
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=31536000",
        "Connection": "keep-alive",
        "X-Content-Type-Options": "nosniff"
    }

    if extra_headers:
        headers.update(extra_headers)

    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if request.method == 'HEAD':
        work_loads[client_id] -= 1
        return web.Response(
            status=206 if range_header else 200,
            headers=headers
        )

    async def stream_generator():
        try:
            bytes_sent = 0
            async for chunk in streamer.stream_file(
                media_ref,
                offset=start,
                limit=content_length,
                fallback_message_id=fallback_message_id,
                on_fallback_message=on_fallback_message
            ):
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


@routes.get("/", allow_head=True)
async def root_redirect(request):
    raise web.HTTPFound("https://github.com/fyaz05/FileToLink")


@routes.get("/health", allow_head=True)
async def health_endpoint(request):
    return web.json_response({"status": "ok"})


@routes.get("/metrics", allow_head=True)
async def metrics_endpoint(request):
    _require_admin_token(request)
    return web.Response(
        text=get_metrics_text(),
        content_type="text/plain; version=0.0.4",
    )


@routes.get("/status", allow_head=True)
async def status_endpoint(request):
    _require_admin_token(request)
    uptime = time.time() - StartTime
    total_load = sum(work_loads.values())
    workload_distribution = {str(k): v for k, v in sorted(work_loads.items())}
    bot_username = await _get_cached_bot_username()

    return web.json_response(
        {
            "status": "operational",
            "version": __version__,
            "uptime": get_readable_time(uptime),
            "active_clients": len(multi_clients),
            "total_workload": total_load,
        }
    )


@routes.get(r"/watch/f/{secure_hash}/{name:.+}", allow_head=True)
async def canonical_media_preview(request: web.Request):
    try:
        secure_hash = validate_public_hash(request.match_info["secure_hash"])
        file_record = await get_file_by_hash(secure_hash, raise_on_error=False)
        if not file_record:
            raise FileNotFound("Canonical file not found")

        file_name = file_record.get("file_name") or f"file_{secure_hash}"
        src = f"{Var.URL.rstrip('/')}/f/{secure_hash}/{quote_media_name(file_name)}"
        rendered_page = await render_media_page(file_name, src, requested_action='stream')

        response = web.Response(
            text=rendered_page,
            content_type='text/html',
            headers={"X-Content-Type-Options": "nosniff"}
        )
        response.enable_compression()
        return response
    except (InvalidHash, FileNotFound) as e:
        raise web.HTTPNotFound(text="Resource not found") from e
    except Exception as e:
        error_id = secrets.token_hex(6)
        logger.error(f"Canonical preview error {error_id}: {e}", exc_info=True)
        raise web.HTTPInternalServerError(text=f"Server error: {error_id}") from e


@routes.get(r"/watch/{path:.+}", allow_head=True)
async def media_preview(request: web.Request):
    try:
        path = request.match_info["path"]
        message_id, secure_hash = parse_media_request(path, request.query)
        rendered_page = await render_page(message_id, secure_hash, requested_action='stream')

        response = web.Response(
            text=rendered_page,
            content_type='text/html',
            headers={"X-Content-Type-Options": "nosniff"}
        )
        response.enable_compression()
        return response
    except (InvalidHash, FileNotFound) as e:
        raise web.HTTPNotFound(text="Resource not found") from e
    except RateLimited as e:
        raise web.HTTPTooManyRequests(text=str(e)) from e
    except Exception as e:
        error_id = secrets.token_hex(6)
        logger.error(f"Preview error {error_id}: {e}", exc_info=True)
        raise web.HTTPInternalServerError(text=f"Server error: {error_id}") from e


@routes.get(r"/f/{secure_hash}/{name:.+}", allow_head=True)
async def canonical_media_delivery(request: web.Request):
    try:
        secure_hash = validate_public_hash(request.match_info["secure_hash"])
        file_record = await get_file_by_hash(secure_hash, raise_on_error=False)
        if not file_record:
            raise FileNotFound("Canonical file not found")

        client_id, streamer = select_optimal_client()
        overloaded = work_loads[client_id] >= MAX_CONCURRENT_PER_CLIENT
        work_loads[client_id] += 1
        extra_headers = {"Retry-After": "5"} if overloaded else None

        media_ref = int(file_record["canonical_message_id"])
        fallback_message_id = int(file_record["canonical_message_id"])

        async def persist_refreshed_file_id(message):
            if client_id != 0:
                return
            new_file_id = getattr(message, "remote_file_id", None)
            if new_file_id and new_file_id != file_record.get("file_id"):
                try:
                    await update_cached_file_id(file_record, new_file_id)
                except Exception as e:
                    logger.warning(f"Failed to refresh cached file_id for {secure_hash}: {e}", exc_info=True)

        try:
            return await _serve_media_response(
                request,
                file_info=file_record,
                streamer=streamer,
                client_id=client_id,
                media_ref=media_ref,
                fallback_message_id=fallback_message_id,
                on_fallback_message=persist_refreshed_file_id,
                extra_headers=extra_headers,
            )
        except (FileNotFound, InvalidHash):
            work_loads[client_id] -= 1
            raise
        except Exception:
            work_loads[client_id] -= 1
            raise
    except (InvalidHash, FileNotFound) as e:
        raise web.HTTPNotFound(text="Resource not found") from e
    except Exception as e:
        error_id = secrets.token_hex(6)
        logger.error(f"Canonical stream error {error_id}: {e}", exc_info=True)
        raise web.HTTPInternalServerError(text=f"Server error: {error_id}") from e


@routes.get(r"/{path:.+}", allow_head=True)
async def media_delivery(request: web.Request):
    try:
        path = request.match_info["path"]
        message_id, secure_hash = parse_media_request(path, request.query)

        client_id, streamer = select_optimal_client()
        overloaded = work_loads[client_id] >= MAX_CONCURRENT_PER_CLIENT
        work_loads[client_id] += 1
        extra_headers = {"Retry-After": "5"} if overloaded else None

        try:
            file_info = await streamer.get_file_info(message_id)
            unique_id = _resolve_unique_id(file_info)

            if unique_id[:SECURE_HASH_LENGTH] != secure_hash:
                raise InvalidHash("Provided hash does not match file's unique ID.")

            return await _serve_media_response(
                request,
                file_info=file_info,
                streamer=streamer,
                client_id=client_id,
                media_ref=message_id,
                extra_headers=extra_headers,
            )
        except (FileNotFound, InvalidHash):
            work_loads[client_id] -= 1
            raise
        except Exception:
            work_loads[client_id] -= 1
            raise
    except (InvalidHash, FileNotFound) as e:
        raise web.HTTPNotFound(text="Resource not found") from e
    except Exception as e:
        error_id = secrets.token_hex(6)
        logger.error(f"Server error {error_id}: {e}", exc_info=True)
        raise web.HTTPInternalServerError(text=f"Server error: {error_id}") from e


