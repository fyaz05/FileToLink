import os

from pytdbot import Client

from Thunder.vars import Var

_ENCRYPTION_KEY = b"thunder_tdlib_encryption_key_32b"

StreamBot = Client(
    token=Var.BOT_TOKEN,
    api_id=Var.API_ID,
    api_hash=Var.API_HASH,
    files_directory=os.path.join("tdlib_data", "primary"),
    database_encryption_key=_ENCRYPTION_KEY,
    workers=Var.WORKERS,
    td_verbosity=0,
)

multi_clients = {}
work_loads = {}
