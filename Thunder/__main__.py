# Thunder/__main__.py

import os
import sys
import glob
import asyncio
import importlib.util
from pathlib import Path
from pyrogram import idle
from aiohttp import web
from Thunder.bot import StreamBot
from Thunder.vars import Var
from Thunder.server import web_server
from Thunder.utils.keepalive import ping_server
from Thunder.bot.clients import initialize_clients
from Thunder.utils.logger import logger

# Plugin path
PLUGIN_PATH = "Thunder/bot/plugins/*.py"
plugins = glob.glob(PLUGIN_PATH)

async def start_services():
    """Initializes and starts all essential services for the bot."""

    logger.info("\n================= Starting Telegram Bot Initialization =================")
    try:
        await StreamBot.start()
        bot_info = await StreamBot.get_me()
        StreamBot.username = bot_info.username
        logger.info("----------------- Telegram Bot Initialized Successfully -----------------")
        logger.info("Bot Username: @%s", StreamBot.username)
    except Exception as e:
        logger.error("Failed to initialize the Telegram Bot: %s", e)
        return

    logger.info("\n================= Starting Client Initialization =================")
    try:
        await initialize_clients()
        logger.info("------------------ Clients Initialized Successfully ------------------")
    except Exception as e:
        logger.error("Failed to initialize clients: %s", e)
        return

    logger.info("\n================= Importing Plugins =================")
    for file_path in plugins:
        try:
            plugin_path = Path(file_path)
            plugin_name = plugin_path.stem
            import_path = f"Thunder.bot.plugins.{plugin_name}"
            spec = importlib.util.spec_from_file_location(import_path, plugin_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules[import_path] = module
            logger.info("Successfully imported plugin: %s", plugin_name)
        except Exception as e:
            logger.error("Failed to import plugin %s: %s", plugin_name, e)
    logger.info("------------------ Plugin Importing Completed ------------------")

    if Var.ON_HEROKU:
        logger.info("\n================= Starting Keep-Alive Service =================")
        asyncio.create_task(ping_server())
        logger.info("----------------- Keep-Alive Service Started -----------------")

    logger.info("\n================= Initializing Web Server =================")
    try:
        app_runner = web.AppRunner(await web_server())
        await app_runner.setup()
        bind_address = "0.0.0.0" if Var.ON_HEROKU else Var.BIND_ADDRESS
        site = web.TCPSite(app_runner, bind_address, Var.PORT)
        await site.start()
        logger.info("------------------ Web Server Initialized Successfully ------------------")
        logger.info("Server Address: %s:%s", bind_address, Var.PORT)
    except Exception as e:
        logger.error("Failed to start the web server: %s", e)
        return

    logger.info("\n================= Service Started =================")
    logger.info("Bot User: %s", bot_info.first_name)
    logger.info("Server Running On: %s:%s", bind_address, Var.PORT)
    logger.info("Owner: %s", Var.OWNER_USERNAME)
    if Var.ON_HEROKU:
        logger.info("App URL: %s", Var.FQDN)
    logger.info("====================================================")

    await idle()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(start_services())
    except KeyboardInterrupt:
        logger.info("\n================= Service Stopped by User =================")
    except Exception as e:
        logger.error("An unexpected error occurred: %s", e)
    finally:
        loop.close()
