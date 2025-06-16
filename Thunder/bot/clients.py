# Thunder/bot/clients.py

import asyncio
from pyrogram import Client
from Thunder.vars import Var
from Thunder.utils.config_parser import TokenParser
from Thunder.bot import multi_clients, work_loads, StreamBot
from Thunder.utils.logger import logger


async def initialize_clients():
    """Initializes multiple Pyrogram client instances based on tokens found in the environment."""
    
    print("╠══════════════════ INITIALIZING CLIENTS ═══════════════════╣")
    multi_clients[0] = StreamBot
    work_loads[0] = 0
    print(" ✓ Primary client initialized successfully")
    
    # Parse tokens from the environment
    print("╠═══════════════ PARSING ADDITIONAL TOKENS ═════════════════╣")
    try:
        all_tokens = TokenParser().parse_from_env()
        if not all_tokens:
            print(" ▶ No additional clients found.")
            return
    except Exception as e:
        logger.error(f" ▶ Error parsing additional tokens: {e}")
        print(" ▶ Primary client will be used.")
        return

    print(f" ▶ Found {len(all_tokens)} additional tokens")

    async def start_client(client_id, token):
        """Starts an individual Pyrogram client."""
        try:
            print(f" ▶ Initializing Client ID: {client_id}...")
            if client_id == len(all_tokens):
                await asyncio.sleep(2)
                print(" ▶ Initialization may take a while, please wait...")

            client = await Client(
                name=str(client_id),
                api_id=Var.API_ID,
                api_hash=Var.API_HASH,
                bot_token=token,
                sleep_threshold=Var.SLEEP_THRESHOLD,
                no_updates=True,
                in_memory=True
            ).start()
            work_loads[client_id] = 0
            print(f" ✓ Client ID {client_id} started successfully")
            return client_id, client
        except Exception as e:
            logger.error(f" ✖ Failed to start Client ID {client_id}. Error: {e}")
            return None

    # Start all clients concurrently and filter out any that failed
    print("╠═════════════ STARTING ADDITIONAL CLIENTS ═════════════════╣")
    clients = await asyncio.gather(*[start_client(i, token) for i, token in all_tokens.items() if token])
    clients = [client for client in clients if client]  # Filter out None values

    # Update the global multi_clients dictionary
    multi_clients.update(dict(clients))
    
    if len(multi_clients) > 1:
        Var.MULTI_CLIENT = True
        print("╠══════════════════════ MULTI-CLIENT ═══════════════════════╣")
        print(f" ▶ Multi-Client Mode Enabled")
        print(f" ✓ Total Clients: {len(multi_clients)} (Including primary client)")
        
        # Display workload distribution
        print(" ▶ Initial workload distribution:")
        for client_id, load in work_loads.items():
            print(f"  • Client {client_id}: {load} tasks")
            
    else:
        print("╠═══════════════════════════════════════════════════════════╣")
        print(" ▶ No additional clients were initialized")
        print(" ▶ Primary client will handle all requests")
