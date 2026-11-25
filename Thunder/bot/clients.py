"""Start MULTI_TOKEN clients and distribute workload."""

import asyncio

from pyrogram import Client
from pyrogram.errors import FloodWait, Unauthorized

from Thunder.bot import (
    StreamBot, cleanup_registry, client_bot_ids, client_keys, multi_clients, work_loads,
)
from Thunder.utils.config_parser import TokenParser
from Thunder.utils.logger import logger
from Thunder.utils.preflight import (
    FAIL, WARN, check_secondary_client, token_bot_id, token_rejected_message,
)
from Thunder.vars import Var

def _client_active(client) -> bool:
    return bool(getattr(client, "is_initialized", False) or getattr(client, "is_connected", False))


def _remove_routing(client_id: int) -> None:
    multi_clients.pop(client_id, None)
    work_loads.pop(client_id, None)


def _retain_for_cleanup(client_id: int, client) -> None:
    if _client_active(client):
        cleanup_registry[client_id] = client


async def cleanup_clients():
    clients = {**cleanup_registry, **multi_clients}
    for client_id, client in clients.items():
        # Cancelled starts may leave connected but uninitialized clients.
        if not _client_active(client):
            _remove_routing(client_id)
            cleanup_registry.pop(client_id, None)
            continue
        for _attempt in range(2):
            try:
                try:
                    await client.stop()
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await client.stop()
            except (asyncio.CancelledError, KeyboardInterrupt):
                logger.warning("Client cleanup was interrupted; retrying if still connected")
            except Exception as e:
                logger.error(f"Error stopping client {client_id}: {e}", exc_info=True)
            if not _client_active(client):
                _remove_routing(client_id)
                cleanup_registry.pop(client_id, None)
                break
        else:
            if _client_active(client):
                logger.error(f"Client {client_id} remained active after stop retries")

async def initialize_clients(report=None):
    print("╠══════════════════ INITIALIZING CLIENTS ═══════════════════╣")
    multi_clients[0] = StreamBot
    work_loads[0] = 0
    Var.MULTI_CLIENT = False
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
            report.add(WARN, f"⚠ {warning}")
        else:
            print(f"   ⚠ {warning}")

    if not tokens:
        print("   ◎ No additional clients found.")
        return

    async def start_client(client_id, token):
        """Start one secondary client; return (client_id, client, error)."""
        client = None
        try:
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
            # Register before start() so cancellation cannot hide the client.
            multi_clients[client_id] = client
            work_loads[client_id] = 0
            try:
                await client.start()
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await client.start()
            work_loads[client_id] = 0
            print(f"   ◎ Client ID {client_id} started")
            return client_id, client, None
        except (asyncio.CancelledError, KeyboardInterrupt):
            if client is not None:
                _remove_routing(client_id)
                _retain_for_cleanup(client_id, client)
            raise
        except Exception as e:
            if client is not None and _client_active(client):
                try:
                    await client.stop()
                except (asyncio.CancelledError, KeyboardInterrupt):
                    _remove_routing(client_id)
                    _retain_for_cleanup(client_id, client)
                    raise
                except Exception as stop_error:
                    logger.error(f"Error stopping failed client {client_id}: {stop_error}", exc_info=True)
            if client is not None:
                _remove_routing(client_id)
                _retain_for_cleanup(client_id, client)
            if client is not None and _client_active(client):
                raise RuntimeError(f"Client {client_id} could not be started or stopped") from e
            logger.error(f"   ✖ Failed to start Client ID {client_id}. Error: {e}", exc_info=True)
            return client_id, None, e

    # Serialize starts to avoid Telegram flood limits.
    started = []
    for client_id, (_key, token) in tokens.items():
        if not token:
            continue
        if started:
            await asyncio.sleep(1)
        started.append(await start_client(client_id, token))
    clients = [(client_id, client) for client_id, client, _error in started if client]
    clients_by_id = dict(clients)
    errors = {client_id: error for client_id, _client, error in started if error is not None}

    client_keys.clear()
    client_bot_ids.clear()

    if report is not None:
        started_ids = {client_id for client_id, _client in clients}
        for client_id, (key, token) in tokens.items():
            if client_id not in started_ids:
                error = errors.get(client_id)
                if isinstance(error, Unauthorized):
                    report.add(FAIL, token_rejected_message(key))
                else:
                    report.add(WARN, f"⚠ {key}: could not be started.\n"
                                       f"  See the log for the reason. Remove the {key}\n"
                                       f"  line from config.env to stop trying it.")
                continue

            client = clients_by_id[client_id]
            try:
                results = await check_secondary_client(
                    client, key, Var.BIN_CHANNEL, token_bot_id(token))
            except asyncio.CancelledError:
                _remove_routing(client_id)
                _retain_for_cleanup(client_id, client)
                raise
            report.extend(results)

            # Broken clients would 404 their share of requests.
            if any(status == FAIL for status, _text in results):
                clients = [pair for pair in clients if pair[0] != client_id]
                _remove_routing(client_id)
                try:
                    await client.stop()
                except (asyncio.CancelledError, KeyboardInterrupt):
                    _retain_for_cleanup(client_id, client)
                    raise
                except Exception as e:
                    logger.error(f"Error stopping unusable client {client_id}: {e}", exc_info=True)
                _retain_for_cleanup(client_id, client)
                if _client_active(client):
                    raise RuntimeError(f"Unusable client {client_id} remained connected")

    kept = {client_id for client_id, _client in clients}
    for client_id, (key, token) in tokens.items():
        if client_id in kept:
            client_keys[client_id] = key
            client_bot_ids[client_id] = token_bot_id(token)

    multi_clients.update(dict(clients))

    Var.MULTI_CLIENT = len(multi_clients) > 1
    if Var.MULTI_CLIENT:
        print("╠══════════════════════ MULTI-CLIENT ═══════════════════════╣")
        print(f"   ◎ Total Clients: {len(multi_clients)} (Including primary client)")

        print("   ▶ Initial workload distribution:")
        for client_id, load in work_loads.items():
            print(f"   • Client {client_id}: {load} tasks")

    else:
        print("╠═══════════════════════════════════════════════════════════╣")
        print("   ▶ No additional clients were initialized")
        print("   ▶ Primary client will handle all requests")
