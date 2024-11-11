# Thunder/bot/clients.py

import asyncio
from pyrogram import Client
from Thunder.vars import Var
from Thunder.utils.config_parser import TokenParser
from Thunder.bot import multi_clients, work_loads, StreamBot
from Thunder.utils.logger import logger

async def initialize_clients():
    """Initializes multiple Pyrogram client instances based on tokens found in the environment."""
    
    logger.info("\n================= Starting Primary Client Initialization =================")
    multi_clients[0] = StreamBot
    work_loads[0] = 0
    logger.info("------------------ Primary Client Initialized Successfully ------------------")

    # Parse tokens from the environment
    logger.info("\n================= Parsing Additional Client Tokens =================")
    all_tokens = TokenParser().parse_from_env()
    if not all_tokens:
        logger.info("No additional clients found. Default client will be used.")
        logger.info("---------------------------------------------------------------------")
        return

    logger.info("------------------ Found %d additional tokens ------------------", len(all_tokens))

    async def start_client(client_id, token):
        """Starts an individual Pyrogram client."""
        try:
            logger.info("Initializing Client ID: %s...", client_id)
            if client_id == len(all_tokens):
                await asyncio.sleep(2)
                logger.info("This is the last client. Initialization may take a while, please wait...")

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
            logger.info("Client ID %s started successfully.", client_id)
            return client_id, client
        except Exception as e:
            logger.error("Failed to start Client ID %s. Error: %s", client_id, e, exc_info=True)

    # Start all clients concurrently and filter out any that failed
    logger.info("\n================= Starting Additional Clients =================")
    clients = await asyncio.gather(*[start_client(i, token) for i, token in all_tokens.items() if token])
    clients = [client for client in clients if client]  # Filter out None values

    # Update the global multi_clients dictionary
    multi_clients.update(dict(clients))
    
    if len(multi_clients) > 1:
        Var.MULTI_CLIENT = True
        logger.info("------------------ Multi-Client Mode Enabled ------------------")
        logger.info("Total Clients Initialized: %d (Including the primary client)", len(multi_clients))
        logger.info("---------------------------------------------------------------------")
    else:
        logger.info("No additional clients were initialized. Default client will be used.")
        logger.info("---------------------------------------------------------------------")

    logger.info("\n================= Client Initialization Completed =================")

