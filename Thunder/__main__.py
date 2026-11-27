"""Start services or run --check; the public-URL probe needs a bound server."""

import asyncio
import glob
import importlib.util
import sys
from datetime import datetime
from functools import partial

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

# Import ConfigError before the package can raise it.
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
    print(render_config_error(config_error, Path(CONFIG_FILE).is_file()), flush=True)
    raise SystemExit(1) from None


PLUGIN_PATH = "Thunder/bot/plugins/*.py"
VERSION = __version__
REQUEST_DRAIN_SECONDS = 25
REQUEST_CANCEL_SECONDS = 5


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
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Background database index ensure failed: {e}", exc_info=True)

    task.add_done_callback(_log_index_failure)
    return task


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
    """Send through the primary bot, which is guaranteed to know the owner."""
    if not Var.OWNER_ID:
        return
    while True:
        try:
            await StreamBot.send_message(Var.OWNER_ID, message)
            return
        except FloodWait as e:
            logger.debug(f"FloodWait in owner alert, sleeping for {e.value}s")
            await asyncio.sleep(e.value)


async def _cleanup_services(resources: dict) -> None:
    """Release every resource created during startup, on every exit path."""
    interrupted = False

    async def wait_for_task(task, seconds: float) -> bool:
        nonlocal interrupted
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                done, _pending = await asyncio.wait({task}, timeout=remaining)
                return task in done
            except KeyboardInterrupt:
                interrupted = True
                task.cancel()

    async def step(name: str, operation):
        # One failed or cancelled cleanup step must not skip the remaining ones.
        nonlocal interrupted
        try:
            await operation()
        except asyncio.CancelledError:
            logger.warning(f"{name} was cancelled during cleanup; continuing")
        except KeyboardInterrupt as e:
            interrupted = True
            logger.error(f"Error during {name}: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error during {name}: {e}", exc_info=True)

    app_runner = resources.get("app_runner")
    if app_runner is not None:
        await step("web server cleanup", app_runner.cleanup)

    background_tasks = resources.get("background_tasks", [])
    await step("rate limiter shutdown", rate_limiter.shutdown)
    for task in background_tasks:
        if task.get_name() != "request_executor_task" or task.done():
            continue
        if not await wait_for_task(task, REQUEST_DRAIN_SECONDS):
            logger.warning("Request executor did not drain within %s seconds", REQUEST_DRAIN_SECONDS)
            task.cancel()
            if not await wait_for_task(task, REQUEST_CANCEL_SECONDS):
                logger.error("Request executor did not stop after cancellation; continuing cleanup")
                continue
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt as e:
            interrupted = True
            logger.error(f"Request executor was interrupted during shutdown: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Request executor failed during shutdown: {e}", exc_info=True)

    for task in background_tasks:
        if task.done() or task.get_name() == "request_executor_task":
            continue
        task.cancel()
        if not await wait_for_task(task, REQUEST_CANCEL_SECONDS):
            logger.error(f"Background task {task.get_name()} did not stop after cancellation")
            continue
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt as e:
            interrupted = True
            logger.error(f"Background task {task.get_name()} was interrupted: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error stopping background task {task.get_name()}: {e}", exc_info=True)

    await step("client cleanup", cleanup_clients)

    # Early failures happen before the primary is added to multi_clients.
    if (getattr(StreamBot, "is_initialized", False)
            or getattr(StreamBot, "is_connected", False)):
        async def stop_primary():
            last_error = None
            for _attempt in range(2):
                try:
                    try:
                        await StreamBot.stop()
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                        await StreamBot.stop()
                except (asyncio.CancelledError, KeyboardInterrupt, Exception) as exc:
                    last_error = exc
                else:
                    last_error = None
                if not (getattr(StreamBot, "is_initialized", False)
                        or getattr(StreamBot, "is_connected", False)):
                    return
            if last_error is not None:
                raise last_error
            raise RuntimeError("primary client remained connected after stop retries")

        await step("primary client cleanup", stop_primary)

    await step("canonical touch task cleanup", drain_background_touch_tasks)

    async def close_database():
        await db.close()
        print("   ✓ Database connection closed")

    await step("database cleanup", close_database)

    if interrupted:
        raise KeyboardInterrupt


async def cleanup_services(resources: dict) -> None:
    """Finish safe teardown even if the caller is cancelled or interrupted again."""
    cleanup_task = asyncio.create_task(_cleanup_services(resources), name="service_cleanup_task")
    cancelled = False
    interrupted = False
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            cancelled = True
        except KeyboardInterrupt:
            interrupted = True
    try:
        cleanup_task.result()
    except BaseException:
        if cancelled:
            raise asyncio.CancelledError from None
        raise
    if interrupted:
        raise KeyboardInterrupt
    if cancelled:
        raise asyncio.CancelledError


async def _connect_primary():
    """Connect the primary client, retrying one Telegram rate limit per call."""
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
    return bot_info


async def check_configuration_only():
    report = preflight.Report()
    exit_code = 1

    try:
        await _connect_primary()
    except Unauthorized:
        report.add("fail", preflight.token_rejected_message("BOT_TOKEN"))
    except Exception as e:
        logger.error(f"Configuration check could not connect to Telegram: {e}", exc_info=True)
        report.add("fail", f"✖ Could not connect to Telegram ({type(e).__name__}: {e}).\n"
                           "  Check BOT_TOKEN, API_ID and API_HASH, then try again.")
    else:
        try:
            await preflight.verify_configuration(report)
        except Exception as e:
            logger.error(f"Configuration preflight aborted: {e}", exc_info=True)
            report.add("fail", "✖ Configuration preflight could not finish; see the log.")
        else:
            if not report.failures:
                try:
                    await initialize_clients(report)
                except Exception as e:
                    logger.error(f"Secondary client initialization aborted: {e}", exc_info=True)
                    report.add("fail", "✖ Secondary client initialization could not finish; see the log.")
        exit_code = 1 if report.failures else 0

    logger.info(report.render(verbose=VERBOSE, corrections=CORRECTIONS))
    raise SystemExit(exit_code)


async def start_services():
    """Start services and guarantee common cleanup on every exit path."""
    resources = {"app_runner": None, "background_tasks": []}
    try:
        return await _start_services(resources)
    finally:
        await cleanup_services(resources)


async def _start_services(resources: dict):
    start_time = datetime.now()

    if CHECK_ONLY:
        return await check_configuration_only()

    print_banner()
    print("╔════════════════ INITIALIZING BOT SERVICES ════════════════╗")

    report = preflight.Report()
    print("   ▶ Starting Telegram Bot initialization...")
    try:
        bot_info = await _connect_primary()
    except ConfigError:
        raise
    except Unauthorized:
        report.add("fail", preflight.token_rejected_message("BOT_TOKEN"))
        logger.info(report.render(verbose=VERBOSE, corrections=CORRECTIONS))
        raise SystemExit(1)
    except Exception as e:
        logger.error(
            f"   ✖ Failed to initialize Telegram Bot: {e}", exc_info=True
        )
        print(f"   ✖ Telegram initialization failed ({type(e).__name__}: {e}).")
        print("     Check BOT_TOKEN, API_ID and API_HASH, then start again.")
        raise SystemExit(1)

    try:
        await preflight.verify_configuration(report)
    except Exception as e:
        logger.error(f"   ✖ Configuration preflight failed: {e}", exc_info=True)
        report.add("fail", "✖ Configuration preflight could not finish; see the log.")
        logger.info(report.render(verbose=VERBOSE, corrections=CORRECTIONS))
        raise SystemExit(1)

    if report.failures:
        logger.info(report.render(verbose=VERBOSE, corrections=CORRECTIONS))
        raise SystemExit(1)

    try:
        await initialize_clients(report)
    except Exception as e:
        logger.error(f"   ✖ Secondary client initialization failed: {e}", exc_info=True)
        report.add("fail", "✖ Secondary client initialization could not finish; see the log.")
        logger.info(report.render(verbose=VERBOSE, corrections=CORRECTIONS))
        raise SystemExit(1)

    if report.failures:
        logger.info(report.render(verbose=VERBOSE, corrections=CORRECTIONS))
        raise SystemExit(1)

    try:
        await set_commands()
        print("   ✓ Bot commands set successfully.")
        resources["background_tasks"].append(schedule_index_ensure())

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
        resources["background_tasks"].append(request_executor_task)
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
        resources["app_runner"] = app_runner
        await app_runner.setup()
        bind_address = Var.BIND_ADDRESS
        site = web.TCPSite(app_runner, bind_address, Var.PORT)
        await site.start()

        keepalive_task = asyncio.create_task(
            ping_server(), name="keepalive_task"
        )
        resources["background_tasks"].append(keepalive_task)
        print("   ✓ Keep-alive service started")
        token_cleanup_task = asyncio.create_task(
            schedule_token_cleanup(), name="token_cleanup_task"
        )
        resources["background_tasks"].append(token_cleanup_task)
        channel_watch_task = None
        if Var.CHANNEL_WATCH_INTERVAL > 0:
            channel_watch_task = asyncio.create_task(
                watch_channel(
                    notify=notify_owner,
                    probe=partial(preflight.probe_storage_channel,
                                  StreamBot, db, Var.BIN_CHANNEL),
                    interval_seconds=Var.CHANNEL_WATCH_INTERVAL,
                ),
                name="channel_watch_task",
            )
            resources["background_tasks"].append(channel_watch_task)

        url_results = await preflight.check_public_url(Var.URL, StreamBot.username)
        report.extend(url_results)
        refresh_configuration_summary(app[CONFIGURATION_STATUS_KEY], report)
        logger.info(report.render(verbose=VERBOSE, corrections=CORRECTIONS))
        for status, text in url_results:
            if status != preflight.OK:
                print(f"   {text.splitlines()[0]}")
            else:
                print(f"   ✓ {text}")

    except Exception as e:
        logger.error(f"   ✖ Failed to start Web Server: {e}", exc_info=True)
        logger.info(report.render(verbose=VERBOSE, corrections=CORRECTIONS))
        raise SystemExit(1)

    elapsed_time = (datetime.now() - start_time).total_seconds()
    print("╠═══════════════════════════════════════════════════════════╣")
    print(f"   ▶ Bot Name: {bot_info.first_name}")
    print(f"   ▶ Username: @{bot_info.username}")
    print(f"   ▶ Links: {Var.URL}")
    print(f"   ▶ Server: {bind_address}:{Var.PORT}")
    print(f"   ▶ Startup Time: {elapsed_time:.2f} seconds")
    print("╚═══════════════════════════════════════════════════════════╝")
    print("   ▶ Bot is now running! Press CTRL+C to stop.")

    try:
        await idle()
    finally:
        print("   ▶ Shutting down services...")


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

def _cancel_and_drain(task) -> None:
    """Cancel the main task and let its cleanup finish before loop shutdown."""
    if task is None or task.done():
        return
    task.cancel()
    while not task.done():
        try:
            LOOP.run_until_complete(task)
        except KeyboardInterrupt:
            # A second Ctrl+C must not strand the same cleanup half-finished.
            task.cancel()
        except (asyncio.CancelledError, SystemExit):
            break
        except Exception as e:
            logger.error(f"Error while draining startup shutdown: {e}", exc_info=True)
            break


if __name__ == '__main__':
    main_task = None
    try:
        main_task = LOOP.create_task(start_services())
        LOOP.run_until_complete(main_task)
    except KeyboardInterrupt:
        _cancel_and_drain(main_task)
        print("╔═══════════════════════════════════════════════════════════╗")
        print("║                   Bot stopped by user (CTRL+C)            ║")
        print("╚═══════════════════════════════════════════════════════════╝")
    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        raise SystemExit(1)
    finally:
        _cancel_and_drain(main_task)
        try:
            LOOP.run_until_complete(LOOP.shutdown_asyncgens())
        except (Exception, KeyboardInterrupt):
            pass
        try:
            LOOP.close()
        except (Exception, KeyboardInterrupt):
            pass
