import os
import sys
import glob
import asyncio
import logging
import importlib
from pathlib import Path
from hydrogram import idle
from .bot import StreamBot
from .vars import Var
from aiohttp import web
from .server import web_server
from .utils.keepalive import ping_server
from Thunder.bot.clients import initialize_clients

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Set specific loggers to ERROR
logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("hydrogram").setLevel(logging.ERROR)

# Plugin path
ppath = "Thunder/bot/plugins/*.py"
files = glob.glob(ppath)

async def start_services():
    # Initialize bot
    try:
        logging.info('Starting Telegram Bot...')
        await StreamBot.start()  # Ensure this is awaited
        bot_info = await StreamBot.get_me()
        StreamBot.username = bot_info.username
        logging.info('Telegram Bot Initialized as: @%s', StreamBot.username)
    except Exception as e:
        logging.error('Failed to initialize bot: %s', e)
        return

    # Initialize clients
    try:
        logging.info('Initializing Clients...')
        await initialize_clients()
        logging.info('Clients Initialized.')
    except Exception as e:
        logging.error('Failed to initialize clients: %s', e)
        return

    # Import plugins
    logging.info('Importing Plugins...')
    for name in files:
        try:
            with open(name) as a:
                patt = Path(a.name)
                plugin_name = patt.stem
                plugins_dir = Path(f"Thunder/bot/plugins/{plugin_name}.py")
                import_path = f".plugins.{plugin_name}"
                spec = importlib.util.spec_from_file_location(import_path, plugins_dir)
                load = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(load)
                sys.modules[f"Thunder.bot.plugins.{plugin_name}"] = load
                logging.info("Imported => %s", plugin_name)
        except Exception as e:
            logging.error('Failed to import plugin %s: %s', name, e)

    # Start keep-alive service if on Heroku
    if Var.ON_HEROKU:
        logging.info('Starting Keep Alive Service...')
        asyncio.create_task(ping_server())

    # Initialize web server
    logging.info('Initializing Web Server...')
    try:
        app = web.AppRunner(await web_server())
        await app.setup()
        bind_address = "0.0.0.0" if Var.ON_HEROKU else Var.BIND_ADRESS
        await web.TCPSite(app, bind_address, Var.PORT).start()
        logging.info('Web Server Initialized on %s:%s.', bind_address, Var.PORT)
    except Exception as e:
        logging.error('Failed to start web server: %s', e)
        return

    # Start the idle loop
    logging.info("\nService Started")
    logging.info("Bot User: %s", bot_info.first_name)
    logging.info("Server running on: %s:%s", bind_address, Var.PORT)
    logging.info("Owner: %s", Var.OWNER_USERNAME)
    if Var.ON_HEROKU:
        logging.info("App running on: %s", Var.FQDN)
    await idle()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(start_services())
    except KeyboardInterrupt:
        logging.info('Service Stopped')
