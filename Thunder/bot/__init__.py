# Thunder/bot/__init__.py

from pyrogram import Client
import pyromod.listen
from Thunder.vars import Var
from os import getcwd
from Thunder.utils.logger import logger

# Initialize the main bot client
StreamBot = Client(
    name="Web Streamer",
    api_id=Var.API_ID,
    api_hash=Var.API_HASH,
    bot_token=Var.BOT_TOKEN,
    sleep_threshold=Var.SLEEP_THRESHOLD,
    workers=Var.WORKERS
)

# Dictionary to hold multiple client instances if needed
multi_clients = {}

# Dictionary to manage workloads and distribution across clients
work_loads = {}
