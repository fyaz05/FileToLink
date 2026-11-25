"""Primary client and shared workload tables."""

from pyrogram import Client
from Thunder.vars import Var

StreamBot = Client(
    name="Web Streamer",
    api_id=Var.API_ID,
    api_hash=Var.API_HASH,
    bot_token=Var.BOT_TOKEN,
    sleep_threshold=Var.SLEEP_THRESHOLD,
    workers=Var.WORKERS,
    max_concurrent_transmissions=1000,
)

multi_clients = {}
work_loads = {}
# Boot-time identity kept for /doctor so it never re-derives a client's slot.
client_keys = {}
client_bot_ids = {}
