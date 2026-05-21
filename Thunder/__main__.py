import asyncio
import glob
import importlib.util
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    from uvloop import install
    install()
except ImportError:
    pass
from aiohttp import web
from pytdbot import types

from Thunder import __version__
from Thunder.bot import StreamBot
from Thunder.bot.clients import cleanup_clients, initialize_clients
from Thunder.server import web_server
from Thunder.utils.canonical_files import drain_background_touch_tasks
from Thunder.utils.commands import set_commands
from Thunder.utils.database import db
from Thunder.utils.keepalive import ping_server
from Thunder.utils.logger import logger
from Thunder.utils.messages import MSG_ADMIN_RESTART_DONE
from Thunder.utils.rate_limiter import rate_limiter, request_executor
from Thunder.utils.tokens import cleanup_expired_tokens
from Thunder.vars import Var

PLUGIN_PATH = "Thunder/bot/plugins/*.py"
VERSION = __version__

_shutdown_event = asyncio.Event()


def _signal_handler():
    logger.info("Shutdown signal received.")
    _shutdown_event.set()


def print_banner():
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


def schedule_index_ensure() -> None:
    task = asyncio.create_task(
        db.ensure_indexes(raise_on_error=False),
        name="ensure_database_indexes"
    )

    def _log_index_failure(done_task: asyncio.Task) -> None:
        try:
            created_indexes = done_task.result()
            if created_indexes:
                print("   ✓ Database indexes ensured.")
            else:
                print("   ▶ Database indexes could not be ensured during startup.")
        except Exception as e:
            logger.error(f"Background database index ensure failed: {e}", exc_info=True)

    task.add_done_callback(_log_index_failure)


async def import_plugins():
    print("╠══════════════════ IMPORTING PLUGINS ════════════════════╣")
    plugins = glob.glob(PLUGIN_PATH)
    if not plugins:
        print("   ▶ No plugins found to import!")
        return 0

    success_count = 0
    failed_plugins = []

    for file_path in plugins:
        try:
            plugin_path = Path(file_path)
            plugin_name = plugin_path.stem
            import_path = f"Thunder.bot.plugins.{plugin_name}"

            spec = importlib.util.spec_from_file_location(
                import_path, plugin_path
            )
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
            logger.error(f"   ✖ Failed to import plugin {plugin_name}: {e}")
            failed_plugins.append(plugin_name)

    print(
        f"   ▶ Total: {len(plugins)} | Success: {success_count} | "
        f"Failed: {len(failed_plugins)}"
    )
    if failed_plugins:
        print(f"   ▶ Failed plugins: {', '.join(failed_plugins)}")

    return success_count


async def start_services():
    import shutil
    import tempfile
    _download_dir = os.path.join(tempfile.gettempdir(), "thunder_downloads")
    await asyncio.to_thread(shutil.rmtree, _download_dir, ignore_errors=True)
    await asyncio.to_thread(os.makedirs, _download_dir, exist_ok=True)

    start_time = datetime.now()
    print_banner()
    print("╔════════════════ INITIALIZING BOT SERVICES ════════════════╗")

    print("   ▶ Starting Telegram Bot initialization...")
    try:
        await StreamBot.start()
        bot_info = await StreamBot.getMe()
        if isinstance(bot_info, types.Error):
            logger.error(f"   ✖ Failed to get bot info: {bot_info.message}")
            return

        bot_username = "unknown"
        if hasattr(bot_info, "usernames") and bot_info.usernames:
            bot_username = bot_info.usernames.editable_username or "unknown"
        else:
            bot_username = getattr(bot_info, "username", "unknown")

        StreamBot.username = bot_username
        print(f"   ✓ Bot initialized successfully as @{StreamBot.username}")

        await set_commands()
        print("   ✓ Bot commands set successfully.")
        schedule_index_ensure()

        restart_message_data = await db.get_restart_message()
        if restart_message_data:
            try:
                await StreamBot.editTextMessage(
                    chat_id=restart_message_data["chat_id"],
                    message_id=restart_message_data["message_id"],
                    text=MSG_ADMIN_RESTART_DONE,
                )
                await db.delete_restart_message(
                    restart_message_data["message_id"]
                )
            except Exception as e:
                logger.error(
                    f"Error processing restart message: {e}", exc_info=True
                )

    except Exception as e:
        logger.error(
            f"   ✖ Failed to initialize Telegram Bot: {e}", exc_info=True
        )
        return

    print("   ▶ Starting Client initialization...")
    try:
        await initialize_clients()
    except Exception as e:
        logger.error(f"   ✖ Failed to initialize clients: {e}", exc_info=True)
        return

    await import_plugins()

    print("   ▶ Starting Request Executor initialization...")
    try:
        request_executor_task = asyncio.create_task(
            request_executor(), name="request_executor_task"
        )
        print("   ✓ Request executor service started")
    except Exception as e:
        logger.error(
            f"   ✖ Failed to start request executor: {e}", exc_info=True
        )
        return

    print("   ▶ Starting Web Server initialization...")
    try:
        app_runner = web.AppRunner(await web_server())
        await app_runner.setup()
        bind_address = Var.BIND_ADDRESS
        site = web.TCPSite(app_runner, bind_address, Var.PORT)
        await site.start()

        keepalive_task = asyncio.create_task(
            ping_server(), name="keepalive_task"
        )
        print("   ✓ Keep-alive service started")
        token_cleanup_task = asyncio.create_task(
            schedule_token_cleanup(), name="token_cleanup_task"
        )

    except Exception as e:
        logger.error(f"   ✖ Failed to start Web Server: {e}", exc_info=True)
        await _cleanup_all(
            background_tasks=[request_executor_task] if 'request_executor_task' in locals() else [],
            app_runner=None
        )
        return

    elapsed_time = (datetime.now() - start_time).total_seconds()
    print("╠═══════════════════════════════════════════════════════════╣")
    print(f"   ▶ Bot Name: {bot_info.first_name}")
    print(f"   ▶ Username: @{bot_username}")
    print(f"   ▶ Server: {bind_address}:{Var.PORT}")
    print(f"   ▶ Startup Time: {elapsed_time:.2f} seconds")
    print("╚═══════════════════════════════════════════════════════════╝")
    print("   ▶ Bot is now running! Press CTRL+C to stop.")

    background_tasks = [
        request_executor_task,
        keepalive_task,
        token_cleanup_task
    ]

    loop = asyncio.get_running_loop()
    if sys.platform != 'win32':
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

    try:
        done, _ = await asyncio.wait(
            [asyncio.create_task(StreamBot.run()), asyncio.create_task(_shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        print("   ▶ Shutting down services...")
        await _cleanup_all(background_tasks=background_tasks, app_runner=app_runner)


async def _cleanup_all(*, background_tasks: list, app_runner=None):
    for task in background_tasks:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    try:
        await StreamBot.stop()
    except Exception as e:
        logger.error(f"Error stopping StreamBot: {e}")

    try:
        await rate_limiter.shutdown()
    except Exception as e:
        logger.error(f"Error during rate limiter cleanup: {e}")

    try:
        await cleanup_clients()
    except Exception as e:
        logger.error(f"Error during client cleanup: {e}")

    try:
        await drain_background_touch_tasks()
    except Exception as e:
        logger.error(f"Error during canonical touch task cleanup: {e}", exc_info=True)

    if app_runner is not None:
        try:
            await app_runner.cleanup()
        except Exception as e:
            logger.error(f"Error during web server cleanup: {e}")

    try:
        await db.close()
        print("   ✓ Database connection closed")
    except Exception:
        logger.error("Error during database cleanup", exc_info=True)


async def schedule_token_cleanup():
    while True:
        try:
            await asyncio.sleep(3 * 3600)
            await cleanup_expired_tokens()
        except asyncio.CancelledError:
            logger.debug("schedule_token_cleanup cancelled cleanly.")
            break
        except Exception as e:
            logger.error(f"Token cleanup error: {e}", exc_info=True)


def main():
    """CLI entry point for the `thunder` console script."""
    try:
        asyncio.run(start_services())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
