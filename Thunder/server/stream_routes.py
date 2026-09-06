# Thunder/server/stream_routes.py

import re
import secrets
import time
from collections.abc import Mapping
from urllib.parse import quote, quote_plus, unquote

from aiohttp import web
from pyrogram.types import Message

from Thunder import StartTime, __version__
from Thunder.bot import StreamBot, multi_clients, work_loads
from Thunder.server.exceptions import FileNotFound, InvalidHash, TelegramUnavailable
from Thunder.utils.bot_utils import quote_media_name
from Thunder.utils.canonical_files import (
    LEGACY_PUBLIC_HASH_LENGTH,
    PUBLIC_HASH_LENGTH,
    forget_stale_record,
    get_file_by_hash,
    touch_buffer_stats,
    update_cached_file_id,
)
from Thunder.utils.custom_dl import ByteStreamer
from Thunder.utils.file_properties import get_media
from Thunder.utils.logger import logger
from Thunder.utils.render_template import render_media_page, render_page
from Thunder.utils.time_format import get_readable_time
from Thunder.vars import Var

routes = web.RouteTableDef()

# legacy 6-char capability hash family (L1: kept while ENABLE_LEGACY_LINKS=on)
SECURE_HASH_LENGTH = 6
CHUNK_SIZE = 1024 * 1024
# M9: per-client admission cap
MAX_CONCURRENT_PER_CLIENT = max(1, Var.MAX_CONCURRENT_STREAMS)
OVERLOAD_RETRY_AFTER_SECONDS = 2
RANGE_REGEX = re.compile(r"^bytes=(?P<start>\d*)-(?P<end>\d*)$")
PATTERN_HASH_FIRST = re.compile(rf"^([a-zA-Z0-9_-]{{{SECURE_HASH_LENGTH}}})(\d+)(?:/.*)?$")
PATTERN_ID_FIRST = re.compile(r"^(\d+)(?:/.*)?$")
VALID_HASH_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")
# L4: both hash families validate side-by-side forever (20 = legacy links,
# 32 = new ingestions), so existing links never break.
VALID_PUBLIC_HASH_REGEX = re.compile(
    rf"^[0-9a-f]{{{LEGACY_PUBLIC_HASH_LENGTH}}}$|^[0-9a-f]{{{PUBLIC_HASH_LENGTH}}}$"
)
VALID_DISPOSITIONS = {"inline", "attachment"}
_ASCII_FALLBACK_RE = re.compile(r"[^A-Za-z0-9._ -]")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "Range, Content-Type, *",
    "Access-Control-Expose-Headers": "Content-Length, Content-Range, Content-Disposition",
}

streamers: dict[int, "ByteStreamer"] = {}


def get_streamer(client_id: int) -> ByteStreamer:
    if client_id not in streamers:
        streamers[client_id] = ByteStreamer(multi_clients[client_id])
    return streamers[client_id]


def parse_media_request(path: str, query: Mapping[str, str]) -> tuple[int, str]:
    clean_path = unquote(path).strip("/")

    match = PATTERN_HASH_FIRST.match(clean_path)
    if match:
        try:
            message_id = int(match.group(2))
            secure_hash = match.group(1)
            if len(secure_hash) == SECURE_HASH_LENGTH and VALID_HASH_REGEX.match(secure_hash):
                return message_id, secure_hash
        except ValueError as e:
            raise InvalidHash(f"Invalid message ID format in path: {e}") from e

    match = PATTERN_ID_FIRST.match(clean_path)
    if match:
        try:
            message_id = int(match.group(1))
            secure_hash = query.get("hash", "").strip()
            if len(secure_hash) == SECURE_HASH_LENGTH and VALID_HASH_REGEX.match(secure_hash):
                return message_id, secure_hash
            else:
                raise InvalidHash("Invalid or missing hash in query parameter")
        except ValueError as e:
            raise InvalidHash(f"Invalid message ID format in path: {e}") from e

    raise InvalidHash("Invalid URL structure or missing hash")


def validate_public_hash(public_hash: str) -> str:
    secure_hash = public_hash.strip().lower()
    if not VALID_PUBLIC_HASH_REGEX.match(secure_hash):
        raise InvalidHash("Invalid canonical file hash")
    return secure_hash


def select_optimal_client() -> tuple[int, ByteStreamer]:
    if not work_loads:
        raise web.HTTPInternalServerError(
            text=("No available clients to handle the request. Please try again later."),
            headers=CORS_HEADERS,
        )

    available_clients = [
        (cid, load) for cid, load in work_loads.items() if load < MAX_CONCURRENT_PER_CLIENT
    ]

    if not available_clients:
        # M9 admission control: refuse instead of stacking unlimited
        # handlers on one client; always advertise Retry-After.
        loads = list(work_loads.values())
        load_range = f"~{min(loads)}–{max(loads)}" if min(loads) != max(loads) else f"~{min(loads)}"
        raise web.HTTPServiceUnavailable(
            text=(
                "All Telegram clients are currently at capacity "
                f"({load_range} active streams). Please retry shortly."
            ),
            headers={
                **CORS_HEADERS,
                "Retry-After": str(OVERLOAD_RETRY_AFTER_SECONDS),
            },
        )

    client_id = min(available_clients, key=lambda x: x[1])[0]
    return client_id, get_streamer(client_id)


def get_content_disposition(request: web.Request) -> str:
    disposition = request.query.get("disposition", "attachment").strip().lower()
    return disposition if disposition in VALID_DISPOSITIONS else "attachment"


def build_content_disposition(disposition: str, filename: str) -> str:
    """L6: RFC 5987 ``filename*`` plus an ASCII fallback so non-Latin names
    survive on clients that ignore RFC 5987."""
    ascii_name = _ASCII_FALLBACK_RE.sub("_", filename).strip() or "file"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


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
        # RFC 7233 §2.1: a last-byte-pos >= length means "rest of the
        # representation" -- clamp instead of rejecting.  Download managers
        # commonly send a fixed-chunk end computed without knowing the size;
        # a hard 416 broke resume/seeking for exactly those clients.
        end = min(end, file_size - 1)
    else:
        if not end_str:
            raise web.HTTPBadRequest(text=f"Invalid range header: {range_header}")
        suffix_len = int(end_str)
        if suffix_len <= 0:
            raise web.HTTPRequestRangeNotSatisfiable(
                headers={"Content-Range": f"bytes */{file_size}"}
            )
        start = max(file_size - suffix_len, 0)
        end = file_size - 1

    if start < 0 or start >= file_size or start > end:
        # L6: 416 discipline with Content-Range
        raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{file_size}"})

    return start, end


def _resolve_filename(file_info: dict, mime_type: str) -> str:
    filename = file_info.get("file_name")
    if filename:
        return filename

    ext = mime_type.split("/")[-1] if "/" in mime_type else "bin"
    ext_map = {"jpeg": "jpg", "mpeg": "mp3", "octet-stream": "bin"}
    ext = ext_map.get(ext, ext)
    return f"file_{secrets.token_hex(4)}.{ext}"


def _resolve_unique_id(file_info: dict) -> str:
    unique_id = file_info.get("unique_id") or file_info.get("file_unique_id")
    if not unique_id:
        raise FileNotFound("File unique ID not found in info.")
    return unique_id


async def _serve_media_response(
    request: web.Request,
    *,
    file_info: dict,
    streamer: ByteStreamer,
    client_id: int,
    media_ref: int | Message,
):
    file_size = int(file_info.get("file_size", 0) or 0)
    if file_size == 0:
        raise FileNotFound("File size is reported as zero or unavailable.")

    range_header = request.headers.get("Range", "")
    start, end = parse_range_header(range_header, file_size)
    content_length = end - start + 1

    if start == 0 and end == file_size - 1:
        range_header = ""

    mime_type = file_info.get("mime_type") or "application/octet-stream"
    filename = _resolve_filename(file_info, mime_type)
    disposition = get_content_disposition(request)

    headers = {
        "Content-Type": mime_type,
        "Content-Length": str(content_length),
        "Content-Disposition": build_content_disposition(disposition, filename),
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=31536000",
        "Connection": "keep-alive",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Range, Content-Type, *",
        "Access-Control-Expose-Headers": ("Content-Length, Content-Range, Content-Disposition"),
        "X-Content-Type-Options": "nosniff",
    }

    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if request.method == "HEAD":
        work_loads[client_id] -= 1
        return web.Response(status=206 if range_header else 200, headers=headers)

    async def stream_generator():
        try:
            bytes_sent = 0
            bytes_to_skip = start % CHUNK_SIZE

            async for chunk in streamer.stream_file(
                media_ref,
                offset=start,
                limit=content_length,
            ):
                if bytes_to_skip > 0:
                    if len(chunk) <= bytes_to_skip:
                        bytes_to_skip -= len(chunk)
                        continue
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
        status=206 if range_header else 200, body=stream_generator(), headers=headers
    )


@routes.get("/", allow_head=True)
async def root_redirect(request):
    raise web.HTTPFound("https://github.com/fyaz05/FileToLink")


@routes.get("/health", allow_head=True)
async def health_endpoint(request):
    """M3: zero-dependency liveness endpoint (keepalive now targets this)."""
    return web.json_response(
        {"status": "ok"},
        headers={"Cache-Control": "no-store"},
    )


_ACTIVATION_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")


def _is_activation_token(token: str) -> bool:
    """Strict shape check for tokens issued by tokens.generate().

    Activation tokens are ``secrets.token_urlsafe(32)`` -- exactly 43
    URL-safe base64 chars.  Validating the shape before interpolation into
    the t.me redirect keeps untrusted input out of the Location header
    (CodeQL py/url-redirection) and skips a DB roundtrip for garbage input.
    """
    return bool(_ACTIVATION_TOKEN_RE.fullmatch(token))


def _telegram_activate_url(username: str, token: str) -> str:
    """Build the t.me deep link with the token percent-encoded.

    ``quote_plus(safe='')`` guarantees the token can only ever occupy the
    query-value slot (no ``&``/``#``/CR/LF can reshape the URL) -- a no-op
    for shape-valid tokens, which are already URL-safe.

    Built with ``+`` concatenation, not an f-string: the redirect target is
    then provably constant-prefixed ("https://t.me/"), which is exactly the
    safety property CodeQL's py/url-redirection sanitizer model recognizes
    (right-operand-of-concat sanitizer; formatting is not modeled).  Do not
    "modernize" this back to an f-string -- it would re-flag the alert.
    """
    return "https://t.me/" + username + "?start=" + quote_plus(token, safe="")


@routes.get("/activate/{token}")
async def activate_endpoint(request: web.Request):
    """M8: web entry for activation -- shorteners can produce real URLs."""
    token = request.match_info.get("token", "").strip()
    username = getattr(StreamBot, "username", None)
    if not token:
        raise web.HTTPBadRequest(text="Missing activation token")
    if not _is_activation_token(token):
        raise web.HTTPBadRequest(text="Malformed activation token")
    if not username:
        raise web.HTTPServiceUnavailable(text="Bot is still starting; try again shortly.")
    raise web.HTTPFound(_telegram_activate_url(username, token))


@routes.get("/status", allow_head=True)
async def status_endpoint(request):
    uptime = time.time() - StartTime
    total_load = sum(work_loads.values())
    workload_distribution = {str(k): v for k, v in sorted(work_loads.items())}

    dc_id = getattr(getattr(StreamBot, "session", None), "dc_id", None)

    return web.json_response(
        {
            "server": {
                "status": "operational",
                "version": __version__,
                "uptime": get_readable_time(uptime),
            },
            "telegram_bot": {
                "username": f"@{getattr(StreamBot, 'username', None) or 'unknown'}",
                "active_clients": len(multi_clients),
                "dc_id": dc_id,
            },
            "resources": {
                "total_workload": total_load,
                "inflight": total_load,
                "workload_distribution": workload_distribution,
                "touch_buffer": touch_buffer_stats(),
            },
        },
        headers={
            "Access-Control-Allow-Origin": "*",
            # L3: status is dynamic -- never serve it from cache
            "Cache-Control": "no-store",
        },
    )


@routes.options(r"/{path:.+}")
async def media_options(request: web.Request):
    return web.Response(headers={**CORS_HEADERS, "Access-Control-Max-Age": "86400"})


@routes.get(r"/watch/f/{secure_hash}/{name:.+}", allow_head=True)
async def canonical_media_preview(request: web.Request):
    try:
        secure_hash = validate_public_hash(request.match_info["secure_hash"])
        file_record = await get_file_by_hash(secure_hash, raise_on_error=False)
        if not file_record:
            raise FileNotFound("Canonical file not found")

        file_name = file_record.get("file_name") or f"file_{secure_hash}"
        src = f"{Var.URL.rstrip('/')}/f/{secure_hash}/{quote_media_name(file_name)}"
        rendered_page = await render_media_page(
            file_name,
            src,
            mime_type=file_record.get("mime_type"),
        )

        response = web.Response(
            text=rendered_page,
            content_type="text/html",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Range, Content-Type, *",
                "X-Content-Type-Options": "nosniff",
                # M2: player pages are per-file dynamic; keep them unindexed
                "X-Robots-Tag": "noindex, nofollow",
            },
        )
        response.enable_compression()
        return response
    except (InvalidHash, FileNotFound) as e:
        logger.debug(f"Canonical preview error: {type(e).__name__} - {e}", exc_info=True)
        raise web.HTTPNotFound(text="Resource not found") from e
    except Exception as e:
        error_id = secrets.token_hex(6)
        logger.error(f"Canonical preview error {error_id}: {e}", exc_info=True)
        raise web.HTTPInternalServerError(text=f"Server error occurred: {error_id}") from e


@routes.get(r"/watch/{path:.+}", allow_head=True)
async def media_preview(request: web.Request):
    # L1: the legacy URL family can be switched off explicitly.
    if not Var.ENABLE_LEGACY_LINKS:
        raise web.HTTPGone(
            text="Legacy links are disabled on this server. "
            "Please re-send the file to the bot to get a fresh link."
        )
    try:
        path = request.match_info["path"]
        message_id, secure_hash = parse_media_request(path, request.query)

        rendered_page = await render_page(message_id, secure_hash)

        response = web.Response(
            text=rendered_page,
            content_type="text/html",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Range, Content-Type, *",
                "X-Content-Type-Options": "nosniff",
                "X-Robots-Tag": "noindex, nofollow",
            },
        )
        response.enable_compression()
        return response

    except (InvalidHash, FileNotFound) as e:
        logger.debug(f"Client error in preview: {type(e).__name__} - {e}", exc_info=True)
        raise web.HTTPNotFound(text="Resource not found") from e
    except Exception as e:
        error_id = secrets.token_hex(6)
        logger.error(f"Preview error {error_id}: {e}", exc_info=True)
        raise web.HTTPInternalServerError(text=f"Server error occurred: {error_id}") from e


@routes.get(r"/f/{secure_hash}/{name:.+}", allow_head=True)
async def canonical_media_delivery(request: web.Request):
    try:
        secure_hash = validate_public_hash(request.match_info["secure_hash"])
        file_record = await get_file_by_hash(secure_hash, raise_on_error=False)
        if not file_record:
            raise FileNotFound("Canonical file not found")

        client_id, streamer = select_optimal_client()
        work_loads[client_id] += 1

        try:
            _resolve_unique_id(file_record)
            media_ref = int(file_record["canonical_message_id"])

            # M10: resolve the vault message up-front.  One fetch serves
            # both the self-heal check and the Content-Length verification;
            # the Message object is passed on so stream_file does not
            # re-fetch it.
            try:
                vault_message = await streamer.get_message(media_ref)
            except FileNotFound:
                await forget_stale_record(file_record)
                raise FileNotFound(
                    "Vault message missing; record self-healed, re-upload to regenerate the link"
                ) from None
            # TelegramUnavailable (FloodWait-exhaustion / timeout / transport)
            # is NOT proof the vault message is gone: it must not delete the
            # record.  It falls through to the 503 ladder below.

            media = get_media(vault_message)
            if not media:
                await forget_stale_record(file_record)
                raise FileNotFound("Vault message has no media; record self-healed")

            serve_info = dict(file_record)
            actual_size = int(getattr(media, "file_size", 0) or 0)
            if actual_size and actual_size != int(serve_info.get("file_size", 0) or 0):
                # no capability hash in logs: hash+size would fingerprint the
                # link for anyone who later reads /log output
                logger.warning(
                    f"Record size {serve_info.get('file_size')} != vault size {actual_size}; "
                    "serving verified length"
                )
            if actual_size:
                # never tell clients a Content-Length the upstream cannot deliver
                serve_info["file_size"] = actual_size
            serve_info.setdefault("mime_type", None)
            if not serve_info.get("mime_type"):
                serve_info["mime_type"] = getattr(media, "mime_type", None)

            new_file_id = getattr(media, "file_id", None)
            if new_file_id and new_file_id != file_record.get("file_id") and client_id == 0:
                try:
                    await update_cached_file_id(file_record, new_file_id)
                except Exception as e:
                    logger.warning(
                        f"Failed to refresh cached file_id for a canonical file: {e}",
                        exc_info=True,
                    )

            return await _serve_media_response(
                request,
                file_info=serve_info,
                streamer=streamer,
                client_id=client_id,
                media_ref=vault_message,
            )
        except (FileNotFound, InvalidHash):
            work_loads[client_id] -= 1
            raise
        except TelegramUnavailable as e:
            work_loads[client_id] -= 1
            raise web.HTTPServiceUnavailable(
                text=str(e), headers={**CORS_HEADERS, "Retry-After": "5"}
            ) from e
        except web.HTTPException as e:
            work_loads[client_id] -= 1
            logger.debug(f"Client HTTP error in canonical stream: {e}")
            raise
        except Exception as e:
            work_loads[client_id] -= 1
            error_id = secrets.token_hex(6)
            logger.error(f"Canonical stream error {error_id}: {e}", exc_info=True)
            raise web.HTTPInternalServerError(
                text=f"Server error during streaming: {error_id}"
            ) from e
    except (InvalidHash, FileNotFound) as e:
        logger.debug(f"Canonical client error: {type(e).__name__} - {e}", exc_info=True)
        raise web.HTTPNotFound(text="Resource not found") from e
    except web.HTTPException as e:
        logger.warning(f"HTTP exception in canonical stream: {e}")
        raise
    except Exception as e:
        error_id = secrets.token_hex(6)
        logger.error(f"Canonical server error {error_id}: {e}", exc_info=True)
        raise web.HTTPInternalServerError(
            text=f"An unexpected server error occurred: {error_id}"
        ) from e


@routes.get(r"/{path:.+}", allow_head=True)
async def media_delivery(request: web.Request):
    # L1: legacy delivery route honors the same switch
    if not Var.ENABLE_LEGACY_LINKS:
        raise web.HTTPGone(
            text="Legacy links are disabled on this server. "
            "Please re-send the file to the bot to get a fresh link."
        )
    try:
        path = request.match_info["path"]
        message_id, secure_hash = parse_media_request(path, request.query)

        client_id, streamer = select_optimal_client()

        work_loads[client_id] += 1

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
            )

        except (FileNotFound, InvalidHash):
            work_loads[client_id] -= 1
            raise
        except TelegramUnavailable as e:
            work_loads[client_id] -= 1
            raise web.HTTPServiceUnavailable(
                text=str(e), headers={**CORS_HEADERS, "Retry-After": "5"}
            ) from e
        except web.HTTPException as e:
            work_loads[client_id] -= 1
            logger.debug(f"Client HTTP error in media stream: {e}")
            raise
        except Exception as e:
            work_loads[client_id] -= 1
            error_id = secrets.token_hex(6)
            logger.error(f"Stream error {error_id}: {e}", exc_info=True)
            raise web.HTTPInternalServerError(
                text=f"Server error during streaming: {error_id}"
            ) from e

    except (InvalidHash, FileNotFound) as e:
        logger.debug(f"Client error: {type(e).__name__} - {e}", exc_info=True)
        raise web.HTTPNotFound(text="Resource not found") from e
    except web.HTTPException as e:
        logger.warning(f"HTTP exception in media stream: {e}")
        raise
    except Exception as e:
        error_id = secrets.token_hex(6)
        logger.error(f"Server error {error_id}: {e}", exc_info=True)
        raise web.HTTPInternalServerError(
            text=f"An unexpected server error occurred: {error_id}"
        ) from e
