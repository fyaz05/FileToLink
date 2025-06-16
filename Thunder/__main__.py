# Thunder/__main__.py

import os
import sys
import glob
import asyncio
import importlib.util
from pathlib import Path
from datetime import datetime

from pyrogram import idle
from aiohttp import web
from Thunder import __version__
from Thunder.bot import StreamBot
from Thunder.vars import Var
from Thunder.server import web_server
from Thunder.utils.keepalive import ping_server
from Thunder.bot.clients import initialize_clients
from Thunder.utils.logger import logger
from Thunder.utils.database import db
from Thunder.utils.messages import MSG_ADMIN_RESTART_DONE
from Thunder.utils.tokens import cleanup_expired_tokens


# Constants
PLUGIN_PATH = "Thunder/bot/plugins/*.py"
VERSION = __version__


def print_banner():
    """Print a visually appealing banner at startup."""
    banner = f"""
╔═══════════════════════════════════════════════════════════════════╗
║                                                                   ║
║   ████████╗██╗  ██╗██╗   ██╗███╗   ██╗██████╗ ███████╗██████╗     ║
║   ╚══██╔══╝██║  ██║██║   ██║████╗  ██║██╔══██╗██╔════╝██╔══██╗    ║
║      ██║   ███████║██║   ██║██╔██╗ ██║██║  ██║█████╗  ██████╔╝    ║
║      ██║   ██╔══██║██║   ██║██║╚██╗██║██║  ██║██╔══╝  ██╔══██╗    ║
║      ██║   ██║  ██║╚██████╔╝██║ ╚████║██████╔╝███████╗██║  ██║    ║
║      ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝    ║
║                                                                   ║
║                  File Streaming Bot v{VERSION}                        ║
╚═══════════════════════════════════════════════════════════════════╝
    """
    print(banner)


async def import_plugins():
    """Import all plugins from the plugins directory."""
    print("╠════════════════════ IMPORTING PLUGINS ════════════════════╣")
    plugins = glob.glob(PLUGIN_PATH)
    
    if not plugins:
        print(" ▶ No plugins found to import!")
        return 0
    
    success_count = 0
    failed_plugins = []
    
    for file_path in plugins:
        try:
            plugin_path = Path(file_path)
            plugin_name = plugin_path.stem
            import_path = f"Thunder.bot.plugins.{plugin_name}"
            
            spec = importlib.util.spec_from_file_location(import_path, plugin_path)
            if spec is None or spec.loader is None:
                logger.error(f"Invalid plugin specification for {plugin_name}")
                failed_plugins.append(plugin_name)
                continue
                
            module = importlib.util.module_from_spec(spec)
            sys.modules[import_path] = module
            spec.loader.exec_module(module)
            
            success_count += 1
        except Exception as e:
            plugin_name = Path(file_path).stem
            logger.error(f" ✖ Failed to import plugin {plugin_name}: {e}")
            failed_plugins.append(plugin_name)
    
    print(f" ▶ Total: {len(plugins)} | Success: {success_count} | Failed: {len(failed_plugins)}")
    
    if failed_plugins:
        print(f" ▶ Failed plugins: {', '.join(failed_plugins)}")
        
    return success_count


async def start_services():
    """Initialize and start all essential services for the bot."""
    start_time = datetime.now()
    
    print_banner()
    
    print("╔════════════════ INITIALIZING BOT SERVICES ════════════════╗")
    # Initialize Telegram Bot
    print(" ▶ Starting Telegram Bot initialization...")
    try:
        await StreamBot.start()
        bot_info = await StreamBot.get_me()
        StreamBot.username = bot_info.username
        print(f" ✓ Bot initialized successfully as @{StreamBot.username}")

        # Check for restart message
        restart_message_data = await db.get_restart_message()
        if restart_message_data:
            try:
                await StreamBot.edit_message_text(
                    chat_id=restart_message_data["chat_id"],
                    message_id=restart_message_data["message_id"],
                    text=MSG_ADMIN_RESTART_DONE
                )
                await db.delete_restart_message(restart_message_data["message_id"])
            except Exception as e:
                logger.error(f"Error processing restart message: {e}")
        else:
            pass

    except Exception as e:
        logger.error(f" ✖ Failed to initialize Telegram Bot: {e}")
        return
    
    # Initialize Clients
    print(" ▶ Starting Client initialization...")
    try:
        await initialize_clients()
    except Exception as e:
        logger.error(f" ✖ Failed to initialize clients: {e}")
        return
    # Import Plugins
    await import_plugins()
    
    # Initialize Web Server
    print(" ▶ Starting Web Server initialization...")
    try:
        app_runner = web.AppRunner(await web_server())
        await app_runner.setup()
        bind_address = Var.BIND_ADDRESS
        site = web.TCPSite(app_runner, bind_address, Var.PORT)
        await site.start()
        print(f" ✓ Web Server started at {bind_address}:{Var.PORT}")
        

        asyncio.create_task(ping_server())
        print(" ✓ Keep-alive service started")
        asyncio.create_task(schedule_token_cleanup())
    except Exception as e:
        logger.error(f" ✖ Failed to start Web Server: {e}")
        return
    
    # Print completion message
    elapsed_time = (datetime.now() - start_time).total_seconds()
    print("╠═══════════════════════════════════════════════════════════╣")
    print(f" ▶ Bot Name: {bot_info.first_name}")
    print(f" ▶ Username: @{bot_info.username}")
    print(f" ▶ Server: {bind_address}:{Var.PORT}")
    print(f" ▶ Owner: {Var.OWNER_USERNAME}")
    print(f" ▶ Startup Time: {elapsed_time:.2f} seconds")
    print("╚═══════════════════════════════════════════════════════════╝")
    print(" ▶ Bot is now running! Press CTRL+C to stop.")

    # Keep the bot running
    await idle()

async def schedule_token_cleanup():
    while True:
        await asyncio.sleep(3 * 3600)
        await cleanup_expired_tokens()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(start_services())
    except KeyboardInterrupt:
        print("╔═══════════════════════════════════════════════════════════╗")
        print("║              Bot stopped by user (CTRL+C)                 ║")
        print("╚═══════════════════════════════════════════════════════════╝")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
    finally:
        loop.close()
