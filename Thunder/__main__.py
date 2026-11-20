"""Build the event loop, then start clients and the web server.

The loop is created before the Pyrogram imports on purpose (see below). Run
with ``python -m Thunder``, or ``python -m Thunder --check`` to check config
and exit; the public-URL probe needs the server and runs only in
``start_services``.
"""

import asyncio
import glob
import importlib.util
import sys
from datetime import datetime

from pathlib import Path

# Parse --check before the Thunder imports, which validate config and may raise.
CHECK_ONLY = "--check" in sys.argv[1:]
VERBOSE = "--verbose" in sys.argv[1:] or CHECK_ONLY

# wzgram binds a client to the loop at creation, and Thunder.bot creates one on
# import, so the loop must exist first.
try:
    from uvloop import install
    install()
except ImportError:
    pass

LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

from aiohttp import web
from pyrogram import idle
from pyrogram.errors import FloodWait, MessageNotModified, Unauthorized

from Thunder import __version__

# Bound before the package import below, which is where ConfigError is raised.
from Thunder.utils.config_values import CONFIG_FILE, ConfigError, render_config_error

try:
    from Thunder.bot import StreamBot
    from Thunder.bot.clients import cleanup_clients, initialize_clients
    from Thunder.server import web_server
    from Thunder.utils import preflight
    from Thunder.utils.commands import set_commands
    from Thunder.utils.database import db
    from Thunder.utils.channel_watch import watch_channel
    from Thunder.utils.keepalive import ping_server
    from Thunder.utils.canonical_files import drain_background_touch_tasks
    from Thunder.utils.logger import logger
    from Thunder.utils.messages import MSG_ADMIN_RESTART_DONE
    from Thunder.utils.rate_limiter import rate_limiter, request_executor
    from Thunder.utils.tokens import cleanup_expired_tokens
    from Thunder.server.stream_routes import (
        CONFIGURATION_STATUS_KEY, configuration_summary,
        refresh_configuration_summary,
    )
    from Thunder.vars import CORRECTIONS, Var
except ConfigError as config_error:
    # The file's presence decides the message; the path is checked directly.
    print(render_config_error(config_error, Path(CONFIG_FILE).is_file()), flush=True)
    raise SystemExit(1) from None


PLUGIN_PATH = "Thunder/bot/plugins/*.py"
VERSION = __version__


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
    print("╠════════════════════ IMPORTING PLUGINS ════════════════════╣")
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


async def notify_owner(message: str) -> None:
    """Send the owner a message; failures raise so the watcher can retry.

    Requires ``initialize_clients`` first, which ``start_services`` does before
    creating the watcher, so ``multi_clients`` and ``work_loads`` are populated.
    """
    if not Var.OWNER_ID:
        return
    from Thunder.bot import multi_clients, work_loads
    client = multi_clients[min(work_loads, key=work_loads.get)]
    await client.send_message(Var.OWNER_ID, message)


def make_channel_probe():
    """Bind the channel check to its ids once."""

    async def probe():
        message_id = await preflight.sample_message_id(db)
        return await preflight.storage_channel_problem(
            StreamBot, Var.BIN_CHANNEL, message_id)

    return probe


def emit(report) -> None:
    """Log the block for /log and PaaS viewers; printing would duplicate it."""
    logger.info(report.render(verbose=VERBOSE, corrections=CORRECTIONS))


async def check_configuration_only():
    """``--check``: run the checks, stop the clients, and exit.

    Serves nothing; the public-URL probe needs the server's ``/status`` and
    runs only in ``start_services``.
    """
    report = preflight.Report()
    exit_code = 1

    try:
        await StreamBot.start()
        StreamBot.username = (await StreamBot.get_me()).username
        await preflight.verify_configuration(report)
        await initialize_clients(report)
    except Unauthorized:
        # Same named failure as the normal start path: the token was rejected.
        report.add("fail", preflight.token_rejected_message("BOT_TOKEN"))
    except Exception as e:
        # The summary names only the type; the traceback says why.
        logger.error(f"Configuration check aborted: {e}", exc_info=True)
        report.add("fail", f"✖ Could not connect to Telegram ({type(e).__name__}: {e}).\n"
                           "  Check BOT_TOKEN, API_ID and API_HASH, then try again.")
    else:
        exit_code = 1 if report.failures else 0
    finally:
        try:
            await cleanup_clients()
        except Exception:
            pass
        # Not in multi_clients until initialize_clients runs, so stop it here.
        try:
            if StreamBot.is_initialized:
                await StreamBot.stop()
        except Exception:
            pass
        try:
            await db.close()
        except Exception:
            pass

    emit(report)
    raise SystemExit(exit_code)


async def start_services():
    start_time = datetime.now()

    if CHECK_ONLY:
        return await check_configuration_only()

    print_banner()
    print("╔════════════════ INITIALIZING BOT SERVICES ════════════════╗")

    report = preflight.Report()
    print("   ▶ Starting Telegram Bot initialization...")
    try:
        try:
            await StreamBot.start()
        except FloodWait as e:
            logger.debug(f"FloodWait in bot start, sleeping for {e.value}s")
            await asyncio.sleep(e.value)
            await StreamBot.start()

        try:
            bot_info = await StreamBot.get_me()
        except FloodWait as e:
            logger.debug(f"FloodWait in get_me, sleeping for {e.value}s")
            await asyncio.sleep(e.value)
            bot_info = await StreamBot.get_me()

        StreamBot.username = bot_info.username

    except ConfigError:
        raise
    except Unauthorized:
        # Handle rejected tokens here, or the named failure from check_bot_token
        # is lost to the generic handler.
        report.add("fail", preflight.token_rejected_message("BOT_TOKEN"))
        emit(report)
        raise SystemExit(1)
    except Exception as e:
        logger.error(
            f"   ✖ Failed to initialize Telegram Bot: {e}", exc_info=True
        )
        print("   ✖ Telegram rejected the configuration or could not be reached.")
        print(f"     {type(e).__name__}: {e}")
        print("     Check BOT_TOKEN, API_ID and API_HASH, then start again.")
        raise SystemExit(1)

    try:
        await preflight.verify_configuration(report)
        await initialize_clients(report)
    except Exception as e:
        logger.error(f"   ✖ Failed during configuration checks: {e}", exc_info=True)
        raise SystemExit(1)

    if report.failures:
        emit(report)
        try:
            await cleanup_clients()
        except Exception:
            pass
        try:
            # cleanup_clients() already stopped StreamBot; stop it again only
            # if that failed, so the client is stopped exactly once.
            if StreamBot.is_initialized:
                await StreamBot.stop()
        except Exception:
            pass
        try:
            await db.close()
        except Exception:
            pass
        raise SystemExit(1)

    try:
        await set_commands()
        print("   ✓ Bot commands set successfully.")
        schedule_index_ensure()

        restart_message_data = await db.get_restart_message()
        if restart_message_data:
            try:
                try:
                    await StreamBot.edit_message_text(
                        chat_id=restart_message_data["chat_id"],
                        message_id=restart_message_data["message_id"],
                        text=MSG_ADMIN_RESTART_DONE,
                    )
                except FloodWait as e:
                    logger.debug(f"FloodWait in restart message edit, sleeping for {e.value}s")
                    await asyncio.sleep(e.value)
                    await StreamBot.edit_message_text(
                        chat_id=restart_message_data["chat_id"],
                        message_id=restart_message_data["message_id"],
                        text=MSG_ADMIN_RESTART_DONE,
                    )
                except MessageNotModified:
                    pass
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
        raise SystemExit(1)

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
        raise SystemExit(1)

    print("   ▶ Starting Web Server initialization...")
    try:
        app = await web_server()
        # Set before the runner starts: changing app state afterwards is deprecated.
        app[CONFIGURATION_STATUS_KEY] = configuration_summary(report)
        # shutdown_timeout belongs on AppRunner, not TCPSite, where the kwarg is deprecated.
        app_runner = web.AppRunner(
            app,
            handler_cancellation=True,
            shutdown_timeout=25,
        )
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
        channel_watch_task = None
        if Var.CHANNEL_WATCH_INTERVAL > 0:
            channel_watch_task = asyncio.create_task(
                watch_channel(
                    notify=notify_owner,
                    probe=make_channel_probe(),
                    interval_seconds=Var.CHANNEL_WATCH_INTERVAL,
                ),
                name="channel_watch_task",
            )

        # Needs the server running: asks our own URL for /status.
        url_results = await preflight.check_public_url(Var.URL)
        report.extend(url_results)
        refresh_configuration_summary(app[CONFIGURATION_STATUS_KEY], report)
        # After the probe: the public-URL results belong in the block too.
        emit(report)
        for status, text in url_results:
            if status != preflight.OK:
                print(f"   {text.splitlines()[0]}")
            else:
                print(f"   ✓ {text}")

    except Exception as e:
        logger.error(f"   ✖ Failed to start Web Server: {e}", exc_info=True)
        emit(report)
        if 'app_runner' in locals() and app_runner is not None:
            try:
                await app_runner.cleanup()
            except Exception as cleanup_error:
                logger.error(
                    f"Error during web server cleanup after startup failure: {cleanup_error}",
                    exc_info=True
                )
        if 'request_executor_task' in locals() and not request_executor_task.done():
            request_executor_task.cancel()
            try:
                await request_executor_task
            except asyncio.CancelledError:
                pass
        try:
            await StreamBot.stop()
        except Exception:
            pass
        try:
            await cleanup_clients()
        except Exception:
            pass
        try:
            await rate_limiter.shutdown()
        except Exception:
            pass
        try:
            await db.close()
        except Exception as e:
            logger.error(f"Error during database cleanup: {e}", exc_info=True)
        try:
            await drain_background_touch_tasks()
        except Exception as e:
            logger.error(f"Error during canonical touch task cleanup: {e}", exc_info=True)
        raise SystemExit(1)

    elapsed_time = (datetime.now() - start_time).total_seconds()
    print("╠═══════════════════════════════════════════════════════════╣")
    print(f"   ▶ Bot Name: {bot_info.first_name}")
    print(f"   ▶ Username: @{bot_info.username}")
    # Shows a wrong FQDN before the first link goes out.
    print(f"   ▶ Links: {Var.URL}")
    print(f"   ▶ Server: {bind_address}:{Var.PORT}")
    print(f"   ▶ Startup Time: {elapsed_time:.2f} seconds")
    print("╚═══════════════════════════════════════════════════════════╝")
    print("   ▶ Bot is now running! Press CTRL+C to stop.")

    background_tasks = [
        request_executor_task,
        keepalive_task,
        token_cleanup_task
    ]
    if channel_watch_task is not None:
        background_tasks.append(channel_watch_task)

    try:
        await idle()
    finally:
        print("   ▶ Shutting down services...")

        # Must run before the clients and database close, so in-flight downloads can drain.
        if 'app_runner' in locals() and app_runner is not None:
            try:
                await app_runner.cleanup()
            except Exception as e:
                logger.error(f"Error during web server cleanup: {e}")

        for task in background_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

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

if __name__ == '__main__':
    try:
        LOOP.run_until_complete(start_services())
    except KeyboardInterrupt:
        print("╔═══════════════════════════════════════════════════════════╗")
        print("║                   Bot stopped by user (CTRL+C)            ║")
        print("╚═══════════════════════════════════════════════════════════╝")
    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        raise SystemExit(1)
    finally:
        try:
            LOOP.run_until_complete(LOOP.shutdown_asyncgens())
        except Exception:
            pass
        try:
            LOOP.close()
        except Exception:
            pass
