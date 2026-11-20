"""Start extra MULTI_TOKEN* clients and spread the load.

The primary client is created in Thunder.bot; this module adds the rest and
backs /stats and /status. Each client keeps its env var name so failures name
the operator's line (MULTI_TOKEN1, not MULTI_TOKENS).
"""

import asyncio

from pyrogram import Client
from pyrogram.errors import FloodWait, Unauthorized

from Thunder.bot import StreamBot, multi_clients, work_loads
from Thunder.utils.config_parser import TokenParser
from Thunder.utils.logger import logger
from Thunder.vars import Var

async def cleanup_clients():
    for client in multi_clients.values():
        if not client.is_initialized:
            continue
        try:
            try:
                await client.stop()
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await client.stop()
        except Exception as e:
            logger.error(f"Error stopping client: {e}", exc_info=True)

async def initialize_clients(report=None):
    print("╠══════════════════ INITIALIZING CLIENTS ═══════════════════╣")
    multi_clients[0] = StreamBot
    work_loads[0] = 0
    print("   ✓ Primary client initialized")

    parser = TokenParser()
    try:
        tokens = parser.parse_from_env()
    except Exception as e:
        logger.error(f"   ✖ Error parsing additional tokens: {e}", exc_info=True)
        print("   ▶ Primary client will be used.")
        return

    for warning in parser.warnings:
        if report is not None:
            report.add("warn", f"⚠ {warning}")
        else:
            print(f"   ⚠ {warning}")

    if not tokens:
        print("   ◎ No additional clients found.")
        return

    async def start_client(client_id, token):
        """Start one secondary client; return ``(client_id, client, error)``.

        On success ``error`` is ``None``; on failure ``client`` is ``None`` and
        the error is kept for the report.
        """
        try:
            if client_id == len(tokens):
                await asyncio.sleep(2)
            client = Client(
                api_hash=Var.API_HASH,
                api_id=Var.API_ID,
                bot_token=token,
                in_memory=True,
                name=str(client_id),
                no_updates=True,
                max_concurrent_transmissions=1000,
                sleep_threshold=Var.SLEEP_THRESHOLD
            )
            try:
                await client.start()
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await client.start()
            work_loads[client_id] = 0
            print(f"   ◎ Client ID {client_id} started")
            return client_id, client, None
        except Exception as e:
            logger.error(f"   ✖ Failed to start Client ID {client_id}. Error: {e}", exc_info=True)
            return client_id, None, e

    started = await asyncio.gather(
        *[start_client(i, token) for i, (_key, token) in tokens.items() if token])
    clients = [(client_id, client) for client_id, client, _error in started if client]
    clients_by_id = dict(clients)
    errors = {client_id: error for client_id, _client, error in started if error is not None}

    if report is not None:
        from Thunder.utils.preflight import (
            FAIL, check_secondary_client, token_bot_id, token_rejected_message,
        )
        started_ids = {client_id for client_id, _client in clients}
        for client_id, (key, token) in tokens.items():
            if client_id not in started_ids:
                error = errors.get(client_id)
                if isinstance(error, Unauthorized):
                    # A dead token is an operator error, not a flaky network;
                    # booting without it hides the problem.
                    report.add("fail", token_rejected_message(key))
                else:
                    report.add("warn", f"⚠ {key}: could not be started.\n"
                                       f"  See the log for the reason. Remove the {key}\n"
                                       f"  line from config.env to stop trying it.")
                continue

            client = clients_by_id[client_id]
            results = await check_secondary_client(
                client, key, Var.BIN_CHANNEL, token_bot_id(token))
            report.extend(results)

            # No failover in selection: a broken client 404s every Nth request
            # as if the file were gone.
            if any(status == FAIL for status, _text in results):
                clients = [pair for pair in clients if pair[0] != client_id]
                work_loads.pop(client_id, None)
                try:
                    await client.stop()
                except Exception as e:
                    logger.error(f"Error stopping unusable client {client_id}: {e}")

    multi_clients.update(dict(clients))

    if len(multi_clients) > 1:
        Var.MULTI_CLIENT = True
        print("╠══════════════════════ MULTI-CLIENT ═══════════════════════╣")
        print(f"   ◎ Total Clients: {len(multi_clients)} (Including primary client)")

        print("   ▶ Initial workload distribution:")
        for client_id, load in work_loads.items():
            print(f"   • Client {client_id}: {load} tasks")

    else:
        print("╠═══════════════════════════════════════════════════════════╣")
        print("   ▶ No additional clients were initialized")
        print("   ▶ Primary client will handle all requests")
