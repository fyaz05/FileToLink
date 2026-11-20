"""End-to-end probe: upload a file, create its record, fetch it via HTTP.

Runs inside /tmp/live with the deployment venv. All in one process so a VM
restart mid-run cannot invalidate an earlier step.
"""
import asyncio
import datetime
import io
import sys

sys.path.insert(0, "/tmp/live")
import Thunder.vars  # noqa: F401  (loads config.env)
from Thunder.vars import Var

PORT = Var.PORT
BODY = b"Thunder end-to-end probe\n" * 20


async def main():
    import aiohttp
    from pyrogram import Client
    from Thunder.utils.bot_utils import quote_media_name
    from Thunder.utils.canonical_files import build_public_hash
    from Thunder.utils.database import db

    c = Client(name="e2edb", api_id=Var.API_ID, api_hash=Var.API_HASH,
               bot_token=Var.BOT_TOKEN, in_memory=True, no_updates=True)
    await c.start()
    try:
        data = io.BytesIO(BODY)
        data.name = "e2e_probe.txt"
        msg = await c.send_document(Var.BIN_CHANNEL, data)
        uid = msg.document.file_unique_id
        pub = build_public_hash(uid)
        print(f"1. uploaded  id={msg.id} unique={uid} hash={pub}")

        await db.files_col.replace_one({"public_hash": pub}, {
            "file_unique_id": uid, "public_hash": pub,
            "canonical_message_id": msg.id, "file_id": msg.document.file_id,
            "file_name": "e2e_probe.txt", "file_size": msg.document.file_size,
            "mime_type": "text/plain",
            "created_at": datetime.datetime.now(datetime.timezone.utc),
        }, upsert=True)
        print("2. record stored")

        async with aiohttp.ClientSession() as s:
            url = f"http://127.0.0.1:{PORT}/f/{pub}/e2e_probe.txt"
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=40)) as r:
                body = await r.read()
                print(f"3. GET {r.status} bytes={len(body)} "
                      f"ctype={r.headers.get('Content-Type')}")
                print(f"   content matches: {body == BODY}")
                print(f"   range header: {r.headers.get('Accept-Ranges')}")

            # A range request is what a browser player uses to seek.
            async with s.get(url, headers={"Range": "bytes=0-9"},
                             timeout=aiohttp.ClientTimeout(total=40)) as r2:
                part = await r2.read()
                print(f"4. Range: {r2.status} bytes={len(part)} "
                      f"cr={r2.headers.get('Content-Range')}")

            watch = (f"http://127.0.0.1:{PORT}/watch/{uid[:6]}{msg.id}"
                     f"/{quote_media_name('e2e_probe.txt')}")
            async with s.get(watch, timeout=aiohttp.ClientTimeout(total=40)) as r3:
                page = await r3.text()
                print(f"5. watch page: {r3.status} html={len(page)} "
                      f"has_player={'player' in page.lower()}")

        await db.files_col.delete_one({"public_hash": pub})
        print("6. record cleaned up")
    finally:
        await c.stop()


asyncio.run(main())
