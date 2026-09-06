# Thunder/__main__.py

import asyncio
import glob
import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    from uvloop import install

    install()
except ImportError:
    pass
from aiohttp import web
from pyrogram import idle
from pyrogram.errors import MessageNotModified

from Thunder import __version__
from Thunder.bot import StreamBot, work_loads
from Thunder.bot.clients import (
    _harden_session_files,
    cleanup_clients,
    initialize_clients,
)
from Thunder.server import web_server
from Thunder.utils.canonical_files import drain_background_touch_tasks
from Thunder.utils.commands import set_commands
from Thunder.utils.database import db
from Thunder.utils.flag_cache import flags
from Thunder.utils.keepalive import ping_server
from Thunder.utils.logger import logger
from Thunder.utils.messages import MSG_ADMIN_RESTART_DONE
from Thunder.utils.rate_limiter import rate_limiter, start_executors
from Thunder.utils.safe_call import tg_call
from Thunder.utils.shortener import close_shortener
from Thunder.utils.tokens import cleanup_expired_tokens
from Thunder.vars import Var

PLUGIN_PATH = "Thunder/bot/plugins/*.py"
VERSION = __version__


def print_banner():
    banner = f"""
╔═══════════════════════════════════════════════════════════════════╗
║                                                                   ║
║   ████████╗██╗  ██╗██╗   ██╗███╗   ██╗██████╗ ███████╗██████╗     ║
║   ╚══██╔══╝██║  ██║██║   ██║████╗  ██║██╔══██╗██╔══╝██╔══██╗    ║
║      ██║   ███████║██║   ██║██╔██╗ ██║██║  ██║█████╗  ██████╔╝    ║
║      ██║   ██╔══██║██║   ██║██║╚██╗██║██║  ██║██╔══╝  ██╔══██╗    ║
║      ██║   ██║  ██║╚██████╔╝██║ ╚████║██████╔╝███████╗██║  ██║    ║
║      ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝    ║
║                                                                   ║
║                  File Streaming Bot v{VERSION}                        ║
╚═══════════════════════════════════════════════════════════════════╝
"""
    print(banner)


def schedule_index_ensure() -> asyncio.Task:
    task = asyncio.create_task(
        db.ensure_indexes(raise_on_error=False), name="ensure_database_indexes"
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
    return task


async def import_plugins():
    print("╠════════════════════ IMPORTING PLUGINS ════════════════════╣")
    plugins = sorted(glob.glob(PLUGIN_PATH))  # deterministic registration order
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
            logger.error(f"   ✖ Failed to import plugin {plugin_name}: {e}")
            failed_plugins.append(plugin_name)

    print(f"   ▶ Total: {len(plugins)} | Success: {success_count} | Failed: {len(failed_plugins)}")
    if failed_plugins:
        print(f"   ▶ Failed plugins: {', '.join(failed_plugins)}")

    return success_count


async def start_services():
    start_time = datetime.now()
    background_tasks: list[asyncio.Task] = []
    print_banner()
    print("╔════════════════ INITIALIZING BOT SERVICES ════════════════╗")

    print("   ▶ Starting Telegram Bot initialization...")
    try:
        await tg_call(StreamBot.start)
        bot_info = await tg_call(StreamBot.get_me)
        StreamBot.username = bot_info.username
        print(f"   ✓ Bot initialized successfully as @{StreamBot.username}")

        await set_commands()
        print("   ✓ Bot commands set successfully.")
        # managed background task: cancelled + awaited at shutdown (the old
        # fire-and-forget version leaked as a pending task)
        background_tasks.append(schedule_index_ensure())
        _harden_session_files()

        restart_message_data = await db.get_restart_message()
        if restart_message_data:
            try:
                await tg_call(
                    StreamBot.edit_message_text,
                    chat_id=restart_message_data["chat_id"],
                    message_id=restart_message_data["message_id"],
                    text=MSG_ADMIN_RESTART_DONE,
                    retries=1,
                )
                await db.delete_restart_message(restart_message_data["message_id"])
            except MessageNotModified:
                pass
            except Exception as e:
                logger.error(f"Error processing restart message: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"   ✖ Failed to initialize Telegram Bot: {e}", exc_info=True)
        # M13 contract: a failed boot must exit non-zero, or container
        # restart policies never fire and the box sits dead but "healthy".
        raise SystemExit(1) from e

    print("   ▶ Starting Client initialization...")
    try:
        await initialize_clients()
    except Exception as e:
        logger.error(f"   ✖ Failed to initialize clients: {e}", exc_info=True)
        await _safe_teardown_step(cleanup_clients, "clients (boot failure)")
        raise SystemExit(1) from e

    await import_plugins()

    print("   ▶ Starting Request Executor initialization...")
    try:
        # H6b: small worker pool instead of a single serial executor
        executor_tasks = start_executors()
        print(f"   ✓ Request executor pool started ({len(executor_tasks)} workers)")
    except Exception as e:
        logger.error(f"   ✖ Failed to start request executor: {e}", exc_info=True)
        raise SystemExit(1) from e

    print("   ▶ Starting Web Server initialization...")
    try:
        app_runner = web.AppRunner(await web_server())
        await app_runner.setup()
        bind_address = Var.BIND_ADDRESS
        site = web.TCPSite(app_runner, bind_address, Var.PORT)
        await site.start()

        background_tasks.extend(executor_tasks)

        keepalive_task = asyncio.create_task(ping_server(), name="keepalive_task")
        background_tasks.append(keepalive_task)
        print("   ✓ Keep-alive service started")
        token_cleanup_task = asyncio.create_task(
            schedule_token_cleanup(), name="token_cleanup_task"
        )
        background_tasks.append(token_cleanup_task)
        # H6a: bounded bookkeeping -- periodic sweepers
        limiter_sweeper_task = asyncio.create_task(
            schedule_limiter_sweep(), name="limiter_sweeper_task"
        )
        background_tasks.append(limiter_sweeper_task)
        flag_sweeper_task = asyncio.create_task(flags.run_sweeper(), name="flag_cache_sweeper_task")
        background_tasks.append(flag_sweeper_task)

    except Exception as e:
        logger.error(f"   ✖ Failed to start Web Server: {e}", exc_info=True)
        for t in background_tasks:
            t.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        # mirror shutdown_services ordering: the touch buffer must flush
        # BEFORE db.close, or _bulk_flush runs against a closed client and
        # silently discards every pending increment
        await _safe_teardown_step(rate_limiter.shutdown, "rate limiter")
        await _safe_teardown_step(drain_background_touch_tasks, "touch buffer")
        await _safe_teardown_step(cleanup_clients, "clients")
        await _safe_teardown_step(db.close, "database")
        raise SystemExit(1) from e

    elapsed_time = (datetime.now() - start_time).total_seconds()
    print("╠═══════════════════════════════════════════════════════════╣")
    print(f"   ▶ Bot Name: {bot_info.first_name}")
    print(f"   ▶ Username: @{bot_info.username}")
    print(f"   ▶ Server: {bind_address}:{Var.PORT}")
    print(f"   ▶ Startup Time: {elapsed_time:.2f} seconds")
    print("╚═══════════════════════════════════════════════════════════╝")
    print("   ▶ Bot is now running! Press CTRL+C to stop.")

    try:
        await idle()
    finally:
        # M13: ordered teardown with a bounded drain + error aggregation
        await shutdown_services(background_tasks, app_runner)


async def _safe_teardown_step(step, name: str, errors: list | None = None):
    try:
        await asyncio.wait_for(step(), timeout=30)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Error during {name} cleanup: {e}", exc_info=True)
        if errors is not None:
            errors.append((name, e))


async def shutdown_services(background_tasks, app_runner) -> None:
    """M13: restart-marker-safe, bounded drain, aggregated errors."""
    from Thunder.utils.canonical_files import touch_buffer_stats

    print("   ▶ Shutting down services...")
    errors: list = []

    # 1. stop accepting new work
    for task in background_tasks:
        if not task.done():
            task.cancel()

    # one bounded wait for the WHOLE batch (the old per-task wait_for(x, 10)
    # could stack to ~80s worst-case before teardown ever started)
    if background_tasks:
        done, pending = await asyncio.wait(background_tasks, timeout=10)
        for t in done:
            if t.cancelled():
                continue
            exc = t.exception()
            if exc is not None:
                errors.append((t.get_name(), exc))
                logger.error(f"Background task {t.get_name()} failed at shutdown: {exc}")
        for t in pending:
            logger.warning(f"Background task {t.get_name()} did not stop within 10s")

    # 2. bounded drain: wait (<= 30 s) for in-flight streams to finish
    loop = asyncio.get_running_loop()
    drain_deadline = loop.time() + 30
    while sum(work_loads.values()) > 0 and loop.time() < drain_deadline:
        await asyncio.sleep(0.25)
    remaining = sum(work_loads.values())
    if remaining:
        logger.warning(f"Drain deadline hit with {remaining} stream(s) still active.")

    # 3. ordered teardown
    await _safe_teardown_step(rate_limiter.shutdown, "rate limiter", errors)
    await _safe_teardown_step(drain_background_touch_tasks, "touch buffer", errors)
    await _safe_teardown_step(close_shortener, "shortener", errors)
    await _safe_teardown_step(cleanup_clients, "clients", errors)

    if app_runner is not None:
        try:
            await asyncio.wait_for(app_runner.cleanup(), timeout=30)
        except Exception as e:
            errors.append(("web server", e))
            logger.error(f"Error during web server cleanup: {e}")

    await _safe_teardown_step(db.close, "database", errors)
    if not errors:
        print("   ✓ Database connection closed")

    logger.info(f"Touch buffer final state: {touch_buffer_stats()}")

    # M13: aggregate, log everything, exit non-zero when anything failed
    if errors:
        logger.error(
            f"Shutdown completed with {len(errors)} error(s): "
            + ", ".join(name for name, _ in errors)
        )
        sys.exit(1)


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


async def schedule_limiter_sweep():
    """H6a: prune limiter bookkeeping every 5 minutes."""
    while True:
        try:
            await asyncio.sleep(300)
            stats = await rate_limiter.sweep()
            logger.debug(f"Limiter sweep: {stats}")
        except asyncio.CancelledError:
            logger.debug("schedule_limiter_sweep cancelled cleanly.")
            break
        except Exception as e:
            logger.error(f"Limiter sweep error: {e}", exc_info=True)


if __name__ == "__main__":
    # L5: session files carry bearer-equivalent auth keys -- a restrictive
    # umask covers the window between file creation and _harden_session_files
    os.umask(0o077)
    try:
        asyncio.run(start_services())
    except KeyboardInterrupt:
        print("╔═══════════════════════════════════════════════════════════╗")
        print("║                   Bot stopped by user (CTRL+C)            ║")
        print("╚═══════════════════════════════════════════════════════════╝")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
