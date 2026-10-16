import asyncio
import contextlib
import re
import secrets
import time
from collections.abc import Mapping
from urllib.parse import quote, quote_plus, unquote

from aiohttp import web
from pymongo.errors import PyMongoError
from pyrogram.errors import FloodWait
from pyrogram.types import Message

from Thunder import StartTime, __version__
from Thunder.bot import StreamBot, multi_clients, work_loads
from Thunder.server.exceptions import (
    CORS_HEADERS as _CORS_HEADERS,
)
from Thunder.server.exceptions import (
    ERROR_HEADERS,
    RETRY_5,
    FileNotFound,
    InvalidHash,
    TelegramUnavailable,
    unavailable,
)
from Thunder.utils.bot_utils import quote_media_name
from Thunder.utils.canonical_files import (
    LEGACY_PUBLIC_HASH_LENGTH,
    PUBLIC_HASH_LENGTH,
    forget_stale_record,
    get_file_by_hash,
    touch_buffer_stats,
    update_cached_file_id,
)
from Thunder.utils.custom_dl import CHUNK_SIZE, ByteStreamer
from Thunder.utils.file_properties import get_media
from Thunder.utils.logger import logger
from Thunder.utils.render_template import render_media_page, render_page
from Thunder.utils.time_format import get_readable_time
from Thunder.vars import Var

routes = web.RouteTableDef()

# Legacy 6-char capability hash family (kept while ENABLE_LEGACY_LINKS=on)
SECURE_HASH_LENGTH = 6
# Per-client admission cap
MAX_CONCURRENT_PER_CLIENT = max(1, Var.MAX_CONCURRENT_STREAMS)
OVERLOAD_RETRY_AFTER_SECONDS = 2
RANGE_REGEX = re.compile(r"^bytes=(?P<start>\d*)-(?P<end>\d*)$")
PATTERN_HASH_FIRST = re.compile(rf"^([a-zA-Z0-9_-]{{{SECURE_HASH_LENGTH}}})(\d+)(?:/.*)?$")
PATTERN_ID_FIRST = re.compile(r"^(\d+)(?:/.*)?$")
VALID_HASH_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")
# Both hash families validate side-by-side forever (20 = legacy links,
# 32 = new ingestions), so existing links never break.
VALID_PUBLIC_HASH_REGEX = re.compile(
    rf"^[0-9a-f]{{{LEGACY_PUBLIC_HASH_LENGTH}}}$|^[0-9a-f]{{{PUBLIC_HASH_LENGTH}}}$"
)
VALID_DISPOSITIONS = {"inline", "attachment"}
_ASCII_FALLBACK_RE = re.compile(r"[^A-Za-z0-9._ -]")

CORS_HEADERS = _CORS_HEADERS

# Every error response carries CORS (readable browser errors) + no-store
# (a cached failure must never outlive the underlying problem). Defined once in
# server.exceptions so render_template can shed with the same headers.
_ERROR_HEADERS = ERROR_HEADERS
_RETRY_5 = RETRY_5


def _unavailable(text: str) -> web.HTTPServiceUnavailable:
    return unavailable(text)


streamers: dict[int, "ByteStreamer"] = {}


def get_streamer(client_id: int) -> ByteStreamer:
    if client_id not in streamers:
        streamers[client_id] = ByteStreamer(multi_clients[client_id])
    return streamers[client_id]


def parse_media_request(path: str, query: Mapping[str, str]) -> tuple[int, str]:
    clean_path = unquote(path).strip("/")

    # ids are regex-derived \d+; the guards below only catch absurd 4300+
    # digit ids against int's max_str_digits limit
    match = PATTERN_HASH_FIRST.match(clean_path)
    if match:
        try:
            message_id = int(match.group(2))
        except ValueError:
            raise InvalidHash("Invalid message ID format in path") from None
        secure_hash = match.group(1)
        if len(secure_hash) == SECURE_HASH_LENGTH and VALID_HASH_REGEX.match(secure_hash):
            return message_id, secure_hash

    match = PATTERN_ID_FIRST.match(clean_path)
    if match:
        try:
            message_id = int(match.group(1))
        except ValueError:
            raise InvalidHash("Invalid message ID format in path") from None
        secure_hash = query.get("hash", "").strip()
        if len(secure_hash) == SECURE_HASH_LENGTH and VALID_HASH_REGEX.match(secure_hash):
            return message_id, secure_hash
        raise InvalidHash("Invalid or missing hash in query parameter")

    raise InvalidHash("Invalid URL structure or missing hash")


def validate_public_hash(public_hash: str) -> str:
    secure_hash = public_hash.strip().lower()
    if not VALID_PUBLIC_HASH_REGEX.match(secure_hash):
        raise InvalidHash("Invalid canonical file hash")
    return secure_hash


def select_optimal_client() -> tuple[int, ByteStreamer]:
    if not work_loads:
        raise web.HTTPServiceUnavailable(
            text=("No available clients to handle the request. Please try again later."),
            headers={**_ERROR_HEADERS, "Retry-After": "2"},
        )

    available_clients = [
        (cid, load) for cid, load in work_loads.items() if load < MAX_CONCURRENT_PER_CLIENT
    ]

    if not available_clients:
        # admission control: refuse instead of stacking unlimited
        # handlers on one client; always advertise Retry-After.
        loads = list(work_loads.values())
        load_range = f"~{min(loads)}–{max(loads)}" if min(loads) != max(loads) else f"~{min(loads)}"
        raise web.HTTPServiceUnavailable(
            text=(
                "All Telegram clients are currently at capacity "
                f"({load_range} active streams). Please retry shortly."
            ),
            headers={
                **_ERROR_HEADERS,
                "Retry-After": str(OVERLOAD_RETRY_AFTER_SECONDS),
            },
        )

    client_id = min(available_clients, key=lambda x: x[1])[0]
    return client_id, get_streamer(client_id)


def get_content_disposition(request: web.Request) -> str:
    disposition = request.query.get("disposition", "attachment").strip().lower()
    return disposition if disposition in VALID_DISPOSITIONS else "attachment"


def build_content_disposition(disposition: str, filename: str) -> str:
    """RFC 5987 ``filename*`` plus an ASCII fallback so non-Latin names
    survive on clients that ignore RFC 5987."""
    ascii_name = _ASCII_FALLBACK_RE.sub("_", filename).strip() or "file"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    if not range_header:
        return 0, file_size - 1

    if "," in range_header:
        # Valid multi-range syntax ("bytes=0-1,3-4") that this server does not
        # implement. RFC 9110 lets a server ignore a ranges-specifier it cannot
        # satisfy, and the caller turns a full span into a plain 200. A 400 here
        # would claim the client's whole request is malformed, which some
        # players and proxies act on by abandoning the transfer.
        return 0, file_size - 1

    match = RANGE_REGEX.fullmatch(range_header)
    if not match:
        # Genuinely unparseable input is a real client error. Constant body: the
        # header value is attacker-controlled and must not be reflected.
        raise web.HTTPBadRequest(text="Invalid range header", headers=_ERROR_HEADERS)

    start_str = match.group("start")
    end_str = match.group("end")
    if start_str:
        start = int(start_str)
        end = int(end_str) if end_str else file_size - 1
        # RFC 7233: last-byte-pos >= length means "rest of the representation" --
        # clamp, not 416; download managers send fixed-chunk ends without knowing the size.
        end = min(end, file_size - 1)
    else:
        if not end_str:
            raise web.HTTPBadRequest(text="Invalid range header", headers=_ERROR_HEADERS)
        suffix_len = int(end_str)
        if suffix_len <= 0:
            raise web.HTTPRequestRangeNotSatisfiable(
                headers={**_ERROR_HEADERS, "Content-Range": f"bytes */{file_size}"}
            )
        start = max(file_size - suffix_len, 0)
        end = file_size - 1

    if start >= file_size or start > end:
        # 416 discipline with Content-Range
        raise web.HTTPRequestRangeNotSatisfiable(
            headers={**_ERROR_HEADERS, "Content-Range": f"bytes */{file_size}"}
        )

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


async def _fetch_file_record(secure_hash: str) -> dict | None:
    """Canonical-record lookup with brownout mapping: a DB transport
    failure is NOT absence -- 503 + Retry-After so clients/CDNs retry
    instead of caching a permanent-looking 404."""
    try:
        return await get_file_by_hash(secure_hash)
    except (PyMongoError, TimeoutError) as e:
        logger.error(f"Canonical record lookup failed (transport): {e}", exc_info=True)
        raise _unavailable("File index temporarily unavailable; please retry shortly.") from e


@contextlib.asynccontextmanager
async def _route_ladder(label: str):
    """Shared outer ladder: client errors -> 404, Telegram unavailability
    (FloodWait/transport, P2-6) -> uniform 503 + Retry-After, HTTP
    pass-through, the rest -> error-id 500 (no internals in the body)."""
    try:
        yield
    except (InvalidHash, FileNotFound) as e:
        logger.debug(f"{label}: {type(e).__name__} - {e}")
        raise web.HTTPNotFound(
            text="Resource not found",
            headers=_ERROR_HEADERS,
        ) from e
    except (TelegramUnavailable, FloodWait) as e:
        logger.warning(f"{label}: Telegram unavailable ({type(e).__name__})")
        raise _unavailable("Telegram is temporarily unavailable; please retry shortly.") from e
    except web.HTTPException as e:
        # A 5xx here is either load shedding (_preview_rpc_slot) or an outage
        # already logged further up, and the client already has the status and
        # the Retry-After. Logging one line per shed request means an
        # unauthenticated caller who can trigger the shed can fill the log
        # file that /log uploads -- the flood just moves from ERROR to
        # WARNING. Only 4xx is worth a line.
        if e.status < 500:
            logger.warning(f"HTTP exception in {label}: {e}")
        else:
            logger.debug(f"{label}: shedding/broken with {e.status}: {e}")
        raise
    except Exception as e:
        error_id = secrets.token_hex(6)
        logger.error(f"{label} error {error_id}: {e}", exc_info=True)
        raise web.HTTPInternalServerError(
            text=f"An unexpected server error occurred: {error_id}",
            headers=_ERROR_HEADERS,
        ) from e


@contextlib.asynccontextmanager
async def _admission_ladder(client_id: int, label: str):
    """Shared post-admission ladder: the slot is released on every exit
    path; Telegram unavailability maps to 503 + Retry-After with a constant
    body (the exception text carries internal state).

    Yields the IDEMPOTENT release closure rather than decrementing directly, and
    callers hand the same closure to the body generator and its watchdog. A
    failure between "watchdog created" and "Response returned" used to be
    released twice -- once by the ladder here, once by the watchdog at its
    deadline -- driving work_loads negative, which makes select_optimal_client
    believe there is spare capacity. One closure, released once, no ordering
    hazard.
    """
    release = _slot_release(client_id)
    try:
        yield release
    except (FileNotFound, InvalidHash):
        release()
        raise
    except TelegramUnavailable as e:
        release()
        logger.warning(f"{label}: Telegram unavailable: {e}")
        raise _unavailable("Telegram is temporarily unavailable; please retry shortly.") from e
    except FloodWait as e:
        # defense in depth: get_message converts exhaustions already, but any
        # raw FloodWait reaching admission must shed as 503, never 500
        release()
        logger.warning(f"{label}: Telegram FloodWait: {e.value}s")
        raise _unavailable("Telegram is temporarily unavailable; please retry shortly.") from e
    except web.HTTPException as e:
        release()
        logger.debug(f"Client HTTP error in {label}: {e}")
        raise
    except Exception as e:
        release()
        error_id = secrets.token_hex(6)
        logger.error(f"{label} error {error_id}: {e}", exc_info=True)
        raise web.HTTPInternalServerError(
            text=f"Server error during streaming: {error_id}", headers=_ERROR_HEADERS
        ) from e
    except BaseException:
        # cancellation (client disconnect) must still release the slot,
        # or abandoned tickets pile up into false 503s
        release()
        raise


# A stream slot is released in the body generator's `finally`. If the client
# opens the connection and then stops reading, the transport's write buffer
# fills, the generator is suspended at `yield` forever, and `finally` never
# runs: the slot leaks permanently. EXECUTOR_WORKERS of those and the client
# 503s forever with no in-flight request to show for it.
#
# Bounding the total transfer time caps the leak. It is a WALL-CLOCK deadline,
# not a stall detector, and there is no idle check anywhere in it: any client
# sustaining less than size/1800s gets cut mid-transfer, with Content-Length
# already sent, and must resume with a Range. Concretely, at the 1800s cap that
# is 1.14 MiB/s for a 2 GiB file and 2.28 MiB/s for 4 GiB -- fine on a wired
# connection, not fine on a congested mobile link. A prompt answer to "this
# client is not reading" needs progress-based reaping of live transfers, which is
# a larger change than this branch should absorb. Stated here rather than
# dressed up as idle-only, because an earlier version of this comment claimed
# the deadline "only bites on a socket that has been idle", which is false.
_STREAM_MIN_THROUGHPUT_BYTES_PER_SECOND = 64 * 1024
_STREAM_MIN_SECONDS = 120.0
_STREAM_MAX_SECONDS = 1800.0


def _stream_deadline_seconds(file_size: int) -> float:
    """How long this transfer may take before the slot is reclaimed."""
    need = max(0.0, file_size) / _STREAM_MIN_THROUGHPUT_BYTES_PER_SECOND
    return min(_STREAM_MAX_SECONDS, max(_STREAM_MIN_SECONDS, need))


async def _reclaim_slot_when_stalled(completed, deadline_seconds, release, body):
    """Give the slot back if the transfer is still running at the deadline.

    A watchdog rather than a timeout inside the body generator: a stalled
    reader leaves the generator suspended at a `yield`, so a timeout scoped to
    the generator can never fire -- the consumer's await is what is pending, and
    cancelling that task unwinds nothing inside the generator. A separate task
    does not care where the generator is sitting.

    The slot IS always returned; that is the leak this exists to close.

    Stopping the upstream fetch is best-effort and is NOT guaranteed. `aclose()`
    only works on a generator suspended at a `yield`. While a consumer is
    actively pulling -- which is the normal state, and exactly what a slow
    upstream looks like -- the generator is suspended inside
    `await streamer.stream_file(...)` and `aclose()` raises RuntimeError. The
    fetch then keeps running until the consumer's own teardown or the GC closes
    it. That is a bounded annoyance, not a second slot leak: the counter is
    correct either way.

    Returns when `completed` is set (the stream finished normally) or when this
    task is cancelled by the generator's own cleanup.
    """
    try:
        await asyncio.wait_for(completed.wait(), timeout=deadline_seconds)
        return
    except TimeoutError:
        pass
    except asyncio.CancelledError:
        return
    release()
    if body.ag_running:
        # a consumer is mid-__anext__; closing now would raise. Not an error --
        # the consumer owns the generator and will close it.
        logger.debug("stream slot reclaimed at its deadline; body still in flight")
        return
    # CancelledError is deliberately NOT suppressed here: swallowing it would
    # hide a real cancellation of this watchdog as a quiet success.
    await body.aclose()


def _slot_release(client_id: int):
    """An idempotent release of one admission slot.

    Two paths give the slot back -- the body generator's `finally` and the
    stall watchdog -- and either may get there first. Releasing twice drives
    the counter negative, which makes select_optimal_client believe there is
    spare capacity; that is worse than the leak this exists to prevent. So the
    release is made once, here, and both paths call it.
    """
    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        work_loads[client_id] -= 1

    return release


def _make_body(streamer, media_ref, start, content_length, release_slot, deadline_seconds):
    """Build the response body generator and its slot watchdog.

    Returned separately from the Response so the streaming path can be driven
    directly -- aiohttp wraps the generator in a payload and it is no longer
    iterable from outside, which is exactly the object whose stalled state we
    need to test.
    """
    completed = asyncio.Event()
    holder: dict = {}

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
            completed.set()
            watchdog = holder.get("task")
            if watchdog is not None and not watchdog.done():
                watchdog.cancel()
            release_slot()

    body = stream_generator()
    holder["task"] = asyncio.create_task(
        _reclaim_slot_when_stalled(completed, deadline_seconds, release_slot, body),
        name="stream_slot_watchdog",
    )
    return body


async def _serve_media_response(
    request: web.Request,
    *,
    file_info: dict,
    streamer: ByteStreamer,
    client_id: int,
    media_ref: int | Message,
    release_slot,
):
    file_size = int(file_info.get("file_size", 0) or 0)
    if file_size <= 0:
        # <= not ==: a corrupt or legacy record carrying a negative size would
        # otherwise produce a negative Content-Length
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
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": ("Content-Length, Content-Range, Content-Disposition"),
        "X-Content-Type-Options": "nosniff",
        # sandbox: attacker-uploaded SVG/HTML served inline must not execute
        # in site origin; players fetch bytes cross-origin, unaffected
        "Content-Security-Policy": "sandbox",
    }

    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if request.method == "HEAD":
        release_slot()
        return web.Response(status=206 if range_header else 200, headers=headers)

    # release_slot is REQUIRED, not defaulted to None. The ladder owns one
    # closure per request and shares it with the body generator and the
    # watchdog; minting a second here would decrement twice. A default of None
    # advertised a mode that only worked for GET -- HEAD calls release_slot()
    # before any fallback could run, so omitting it was a TypeError and a 500.

    body = _make_body(
        streamer,
        media_ref,
        start,
        content_length,
        release_slot,
        _stream_deadline_seconds(content_length),
    )

    return web.Response(status=206 if range_header else 200, body=body, headers=headers)


@routes.get("/", allow_head=True)
async def root_redirect(request):
    raise web.HTTPFound("https://github.com/fyaz05/FileToLink")


@routes.get("/health", allow_head=True)
async def health_endpoint(request):
    """Zero-dependency liveness endpoint (keepalive now targets this)."""
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
    """Web entry for activation -- shorteners can produce real URLs."""
    token = request.match_info.get("token", "").strip()
    username = getattr(StreamBot, "username", None)
    if not token or not _is_activation_token(token):
        raise web.HTTPBadRequest(text="Malformed activation token", headers=_ERROR_HEADERS)
    if not username:
        raise web.HTTPServiceUnavailable(
            text="Bot is still starting; try again shortly.",
            headers=_RETRY_5,
        )
    raise web.HTTPFound(
        _telegram_activate_url(username, token),
        headers={"Cache-Control": "no-store"},
    )


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
                "workload_distribution": workload_distribution,
                "touch_buffer": touch_buffer_stats(),
            },
        },
        headers={
            # status is dynamic -- never serve it from cache.  CORS stays so
            # browser dashboards can read it; nothing here is secret.
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
        },
    )


@routes.options(r"/{path:.+}")
async def media_options(request: web.Request):
    return web.Response(headers={**CORS_HEADERS, "Access-Control-Max-Age": "86400"})


@routes.get(r"/watch/f/{secure_hash}/{name:.+}", allow_head=True)
async def canonical_media_preview(request: web.Request):
    async with _route_ladder("canonical preview"):
        secure_hash = validate_public_hash(request.match_info["secure_hash"])
        file_record = await _fetch_file_record(secure_hash)
        if not file_record:
            raise FileNotFound("Canonical file not found")

        file_name = file_record.get("file_name") or f"file_{secure_hash}"
        src = f"{Var.URL.rstrip('/')}/f/{secure_hash}/{quote_media_name(file_name)}"
        rendered_page = await render_media_page(
            file_name,
            src,
            mime_type=file_record.get("mime_type"),
            size_bytes=file_record.get("file_size") or None,
        )

        return _preview_response(rendered_page)


def _preview_response(rendered_page: str) -> web.Response:
    """Both preview routes serve the same unindexed, compressed player page."""
    response = web.Response(
        text=rendered_page,
        content_type="text/html",
        headers={
            "Access-Control-Allow-Origin": "*",
            "X-Content-Type-Options": "nosniff",
            # player pages are per-file dynamic; keep them unindexed
            "X-Robots-Tag": "noindex, nofollow",
        },
    )
    response.enable_compression()
    return response


@routes.get(r"/watch/{path:.+}", allow_head=True)
async def media_preview(request: web.Request):
    # the legacy URL family can be switched off explicitly.
    if not Var.ENABLE_LEGACY_LINKS:
        raise web.HTTPGone(
            text="Legacy links are disabled on this server. "
            "Please re-send the file to the bot to get a fresh link.",
            headers=_ERROR_HEADERS,
        )
    async with _route_ladder("preview"):
        path = request.match_info["path"]
        message_id, secure_hash = parse_media_request(path, request.query)

        rendered_page = await render_page(message_id, secure_hash)

        return _preview_response(rendered_page)


@routes.get(r"/f/{secure_hash}/{name:.+}", allow_head=True)
async def canonical_media_delivery(request: web.Request):
    async with _route_ladder("canonical stream"):
        secure_hash = validate_public_hash(request.match_info["secure_hash"])
        file_record = await _fetch_file_record(secure_hash)
        if not file_record:
            raise FileNotFound("Canonical file not found")

        client_id, streamer = select_optimal_client()
        work_loads[client_id] += 1

        async with _admission_ladder(client_id, "canonical stream") as release_slot:
            # called for its side effect: a record without a unique id cannot be
            # served (there is no vault file to stream), so this is the
            # availability check for the whole route
            _resolve_unique_id(file_record)
            media_ref = int(file_record["canonical_message_id"])

            # one vault fetch serves both the self-heal check and the
            # Content-Length verification; the Message is passed on so stream_file does not re-fetch.
            try:
                vault_message = await streamer.get_message(media_ref)
            except FileNotFound:
                await forget_stale_record(file_record)
                raise FileNotFound(
                    "Vault message missing; record self-healed, re-upload to regenerate the link"
                ) from None
            # TelegramUnavailable (FloodWait/timeout/transport) is NOT proof the
            # vault message is gone -- must not delete the record; the ladder
            # maps it to 503.

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
                release_slot=release_slot,
            )


@routes.get(r"/{path:.+}", allow_head=True)
async def media_delivery(request: web.Request):
    # legacy delivery route honors the same switch
    if not Var.ENABLE_LEGACY_LINKS:
        raise web.HTTPGone(
            text="Legacy links are disabled on this server. "
            "Please re-send the file to the bot to get a fresh link.",
            headers=_ERROR_HEADERS,
        )
    async with _route_ladder("media stream"):
        path = request.match_info["path"]
        message_id, secure_hash = parse_media_request(path, request.query)

        client_id, streamer = select_optimal_client()

        work_loads[client_id] += 1

        async with _admission_ladder(client_id, "media stream") as release_slot:
            # one vault fetch serves both the info derivation and the stream:
            # passing the Message on means stream_file does not re-fetch
            vault_message = await streamer.get_message(message_id)
            file_info = streamer.get_file_info_sync(vault_message)
            unique_id = _resolve_unique_id(file_info)

            if unique_id[:SECURE_HASH_LENGTH] != secure_hash:
                raise InvalidHash("Provided hash does not match file's unique ID.")
            return await _serve_media_response(
                request,
                file_info=file_info,
                streamer=streamer,
                client_id=client_id,
                media_ref=vault_message,
                release_slot=release_slot,
            )
