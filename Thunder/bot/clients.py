import asyncio
import glob
import os

from pyrogram import Client

from Thunder.bot import StreamBot, multi_clients, work_loads
from Thunder.utils.config_parser import TokenParser
from Thunder.utils.logger import logger
from Thunder.utils.safe_call import tg_call
from Thunder.vars import Var


def harden_session_files() -> None:
    """Session files hold credentials; chmod 0600 best-effort."""
    for path in glob.glob("*.session"):
        try:
            os.chmod(path, 0o600)
            logger.debug(f"Hardened session file permissions: {path}")
        except OSError as e:
            logger.warning(f"Could not chmod {path}: {e}")


async def cleanup_clients():
    for client in multi_clients.values():
        try:
            # short per-client budget: the outer teardown bounds the total,
            # and a hung stop must not eat the whole budget for the rest
            await tg_call(client.stop, timeout=10.0)
        except Exception as e:
            logger.error(f"Error stopping client: {e}", exc_info=True)


async def initialize_clients():
    print("╠══════════════════ INITIALIZING CLIENTS ═══════════════════╣")
    multi_clients[0] = StreamBot
    work_loads[0] = 0
    print("   ✓ Primary client initialized")
    try:
        all_tokens = TokenParser().parse_from_env()
        if not all_tokens:
            print("   ◎ No additional clients found.")
            return
    except Exception as e:
        logger.error(f"   ✖ Error parsing additional tokens: {e}", exc_info=True)
        print("   ▶ Primary client will be used.")
        return

    async def start_client(client_id, token):
        try:
            client = Client(
                api_hash=Var.API_HASH,
                api_id=Var.API_ID,
                bot_token=token,
                in_memory=True,
                name=str(client_id),
                no_updates=True,
                max_concurrent_transmissions=1000,
                sleep_threshold=Var.SLEEP_THRESHOLD,
            )
            # session bootstrap may exceed the 30s RPC budget.
            await tg_call(client.start, timeout=90.0)
            work_loads[client_id] = 0
            print(f"   ◎ Client ID {client_id} started")
            return client_id, client
        except Exception as e:
            logger.error(f"   ✖ Failed to start Client ID {client_id}. Error: {e}", exc_info=True)
            return None

    clients = await asyncio.gather(
        *[start_client(i, token) for i, token in all_tokens.items() if token]
    )
    clients = [client for client in clients if client]

    multi_clients.update(dict(clients))

    harden_session_files()

    if len(multi_clients) > 1:
        print("╠══════════════════════ MULTI-CLIENT ═══════════════════════╣")
        print(f"   ◎ Total Clients: {len(multi_clients)} (Including primary client)")

        print("   ▶ Initial workload distribution:")
        for client_id, load in work_loads.items():
            print(f"   • Client {client_id}: {load} tasks")

    else:
        print("╠═══════════════════════════════════════════════════════════╣")
        print("   ▶ No additional clients were initialized")
        print("   ▶ Primary client will handle all requests")
