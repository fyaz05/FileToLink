# Thunder/server/stream_routes.py

import re
import time
import math
import secrets
import mimetypes
import traceback
from aiohttp import web
from urllib.parse import unquote, quote
from Thunder.bot import multi_clients, work_loads, StreamBot
from Thunder.server.exceptions import InvalidHash, FileNotFound
from Thunder import StartTime, __version__
from Thunder.utils.time_format import get_readable_time
from Thunder.utils.custom_dl import ByteStreamer
from Thunder.utils.render_template import render_page
from Thunder.vars import Var
from Thunder.utils.logger import logger

routes = web.RouteTableDef()

@routes.get("/status", allow_head=True)
async def root_route_handler(_):
    return web.json_response({
        "server_status": "running",
        "uptime": get_readable_time(time.time() - StartTime),
        "telegram_bot": "@" + StreamBot.username,
        "connected_bots": len(multi_clients),
        "loads": dict(
            ("bot" + str(c + 1), l)
            for c, (_, l) in enumerate(
                sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
            )
        ),
        "version": __version__,
    })

@routes.get(r"/watch/{path:.+}", allow_head=True)
async def watch_handler(request: web.Request):
    try:
        path = unquote(request.match_info["path"])
        match = re.match(r"^([a-zA-Z0-9_-]{6})(\d+)(?:/.*)?$", path)
        if match:
            secure_hash, id = match.group(1), int(match.group(2))
        else:
            id_match = re.search(r"(\d+)(?:/.*)?", path)
            if id_match:
                id = int(id_match.group(1))
                secure_hash = request.query.get("hash", "")
                if not secure_hash:
                    raise InvalidHash("Missing security hash")
            else:
                raise InvalidHash("Invalid URL")

        return web.Response(
            text=await render_page(id, secure_hash),
            content_type='text/html'
        )
        
    except FileNotFound as e:
        logger.warning(f"Watch404: {str(e)}")
        raise web.HTTPNotFound(text=str(e))
    except InvalidHash as e:
        logger.warning(f"WatchAuth: {str(e)}")
        raise web.HTTPForbidden(text=str(e))
    except Exception as e:
        logger.error(f"WatchError: {str(e)}")
        raise web.HTTPInternalServerError(text="Internal error")

@routes.get(r"/{path:.+}", allow_head=True)
async def download_handler(request: web.Request):
    try:
        path = unquote(request.match_info["path"])
        match = re.match(r"^([a-zA-Z0-9_-]{6})(\d+)(?:/.*)?$", path)
        if match:
            secure_hash, id = match.group(1), int(match.group(2))
        else:
            id_match = re.search(r"(\d+)(?:/.*)?", path)
            if id_match:
                id = int(id_match.group(1))
                secure_hash = request.query.get("hash", "")
                if not secure_hash:
                    raise InvalidHash("Missing security hash")
            else:
                raise InvalidHash("Invalid URL")

        return await media_streamer(request, id, secure_hash)
        
    except FileNotFound as e:
        logger.warning(f"DL404: {str(e)}")
        raise web.HTTPNotFound(text=str(e))
    except InvalidHash as e:
        logger.warning(f"DLAuth: {str(e)}")
        raise web.HTTPForbidden(text=str(e))
    except Exception as e:
        logger.error(f"DLError: {str(e)}")
        raise web.HTTPInternalServerError(text="Internal error")

class_cache = {}

async def media_streamer(request: web.Request, id: int, secure_hash: str):
    try:
        if id <= 0 or len(secure_hash) != 6:
            raise InvalidHash("Invalid request")

        index = min(work_loads, key=work_loads.get)
        faster_client = multi_clients[index]
        
        if faster_client not in class_cache:
            class_cache[faster_client] = ByteStreamer(faster_client)
        tg_connect = class_cache[faster_client]

        file_id = await tg_connect.get_file_properties(id)
        if len(file_id.unique_id) < 6 or file_id.unique_id[:6] != secure_hash:
            raise InvalidHash("Security mismatch")

        file_size = file_id.file_size
        range_header = request.headers.get("Range", "bytes=0-")
        
        # Range handling
        from_bytes, until_bytes = 0, file_size - 1
        if range_header:
            range_parts = range_header.replace("bytes=", "").split("-")
            from_bytes = int(range_parts[0])
            until_bytes = int(range_parts[1]) if range_parts[1] else file_size - 1

        if any([until_bytes > file_size, from_bytes < 0, until_bytes < from_bytes]):
            return web.Response(
                status=416,
                headers={"Content-Range": f"bytes */{file_size}"},
                text="Invalid range"
            )

        chunk_size = 1024 * 1024
        until_bytes = min(until_bytes, file_size - 1)
        offset = from_bytes - (from_bytes % chunk_size)
        
        body = tg_connect.yield_file(
            file_id, index, offset,
            from_bytes - offset,
            (until_bytes % chunk_size) + 1,
            math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size),
            chunk_size
        )

        mime_type = file_id.mime_type or mimetypes.guess_type(file_id.file_name)[0] or "application/octet-stream"
        disposition = "inline" if mime_type.startswith(("video/", "audio/")) else "attachment"
        safe_name = re.sub(r'[^\w.\-]', '_', unquote(file_id.file_name)) if file_id.file_name else f"file_{secrets.token_hex(4)}"

        return web.Response(
            status=206 if range_header else 200,
            body=body,
            headers={
                "Content-Type": mime_type,
                "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
                "Content-Length": str(until_bytes - from_bytes + 1),
                "Content-Disposition": f'{disposition}; filename="{safe_name}"',
                "Accept-Ranges": "bytes"
            }
        )

    except Exception as e:
        logger.error(f"StreamError: {str(e)}")
        raise
