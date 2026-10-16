import asyncio
import glob
import importlib.util
import os
import sys
import time
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
from Thunder.bot import StreamBot, multi_clients, work_loads
from Thunder.bot.clients import (
    cleanup_clients,
    harden_session_files,
    initialize_clients,
)
from Thunder.server import web_server
from Thunder.utils.canonical_files import drain_background_touch_tasks, touch_buffer_stats
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

# [!_]* excludes __init__.py (the package marker) and any _helper module:
# import_plugins loads each match as a plugin, so only handler modules belong
PLUGIN_PATH = "Thunder/bot/plugins/[!_]*.py"
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
        except asyncio.CancelledError:
            return
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
    start_time = time.monotonic()
    background_tasks: list[asyncio.Task] = []
    print_banner()
    print("╔════════════════ INITIALIZING BOT SERVICES ════════════════╗")

    print("   ▶ Starting Telegram Bot initialization...")
    try:
        # session bootstrap (connect + auth) legitimately outlives the
        # 30s lightweight-RPC budget
        await tg_call(StreamBot.start, timeout=90.0)
        bot_info = await tg_call(StreamBot.get_me)
        # pyrogram sets Client.username dynamically, so mypy cannot see it; pin it for /status.
        username = bot_info.username
        StreamBot.username = username  # type: ignore[attr-defined]
        print(f"   ✓ Bot initialized successfully as @{username}")

        await set_commands()
        print("   ✓ Bot commands set successfully.")
        # managed background task: cancelled + awaited at shutdown
        background_tasks.append(schedule_index_ensure())
        harden_session_files()

        restart_message_data = await db.get_restart_message()
        if restart_message_data:
            edited_ok = True
            try:
                await tg_call(
                    StreamBot.edit_message_text,
                    chat_id=restart_message_data["chat_id"],
                    message_id=restart_message_data["message_id"],
                    text=MSG_ADMIN_RESTART_DONE,
                    retries=1,
                )
            except MessageNotModified:
                pass  # a previous boot already wrote it; still clear below
            except Exception as e:
                logger.error(f"Error processing restart message: {e}", exc_info=True)
                edited_ok = False
            if edited_ok:
                try:
                    await db.delete_restart_message(restart_message_data["message_id"])
                except Exception as e:
                    logger.error(f"Error clearing restart marker: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"   ✖ Failed to initialize Telegram Bot: {e}", exc_info=True)
        # the index-ensure task may already be scheduled -- stop it against a
        # closing client BEFORE db.close
        await _cancel_tasks(background_tasks, timeout=10)
        await _safe_teardown_step(StreamBot.stop, "bot (boot failure)")
        await _safe_teardown_step(db.close, "database (boot failure)")
        # a failed boot must exit non-zero or container restart policies never fire.
        raise SystemExit(1) from e

    print("   ▶ Starting Client initialization...")
    try:
        await initialize_clients()
    except Exception as e:
        logger.error(f"   ✖ Failed to initialize clients: {e}", exc_info=True)
        # index-ensure may be live already -- stop it before db.close
        await _cancel_tasks(background_tasks, timeout=10)
        await _safe_teardown_step(cleanup_clients, "clients (boot failure)", cap=_client_stop_cap())
        await _safe_teardown_step(StreamBot.stop, "bot (boot failure)")
        await _safe_teardown_step(db.close, "database (boot failure)")
        raise SystemExit(1) from e

    await import_plugins()

    print("   ▶ Starting Request Executor initialization...")
    try:
        # worker pool; registered early so a later boot failure cancels live workers too
        executor_tasks = start_executors()
        background_tasks.extend(executor_tasks)
        print(f"   ✓ Request executor pool started ({len(executor_tasks)} workers)")
    except Exception as e:
        logger.error(f"   ✖ Failed to start request executor: {e}", exc_info=True)
        await _cancel_tasks(background_tasks, timeout=30)
        await _safe_teardown_step(rate_limiter.shutdown, "rate limiter")
        await _safe_teardown_step(drain_background_touch_tasks, "touch buffer")
        await _safe_teardown_step(close_shortener, "shortener")
        await _safe_teardown_step(cleanup_clients, "clients (boot failure)", cap=_client_stop_cap())
        await _safe_teardown_step(StreamBot.stop, "bot (boot failure)")
        await _safe_teardown_step(db.close, "database (boot failure)")
        raise SystemExit(1) from e

    print("   ▶ Starting Web Server initialization...")
    try:
        app_runner = web.AppRunner(await web_server(), access_log=None)
        await app_runner.setup()
        bind_address = Var.BIND_ADDRESS
        site = web.TCPSite(app_runner, bind_address, Var.PORT)
        await site.start()

        keepalive_task = asyncio.create_task(ping_server(), name="keepalive_task")
        background_tasks.append(keepalive_task)
        print("   ✓ Keep-alive service started")
        token_cleanup_task = asyncio.create_task(
            schedule_token_cleanup(), name="token_cleanup_task"
        )
        background_tasks.append(token_cleanup_task)
        # bounded bookkeeping -- periodic sweepers
        limiter_sweeper_task = asyncio.create_task(
            schedule_limiter_sweep(), name="limiter_sweeper_task"
        )
        background_tasks.append(limiter_sweeper_task)
        flag_sweeper_task = asyncio.create_task(flags.run_sweeper(), name="flag_cache_sweeper_task")
        background_tasks.append(flag_sweeper_task)

    except Exception as e:
        logger.error(f"   ✖ Failed to start Web Server: {e}", exc_info=True)
        await _cancel_tasks(background_tasks, timeout=30)
        # touch buffer must flush BEFORE db.close, or _bulk_flush discards increments
        await _safe_teardown_step(rate_limiter.shutdown, "rate limiter")
        await _safe_teardown_step(drain_background_touch_tasks, "touch buffer")
        await _safe_teardown_step(close_shortener, "shortener")
        await _safe_teardown_step(cleanup_clients, "clients", cap=_client_stop_cap())
        await _safe_teardown_step(db.close, "database")
        raise SystemExit(1) from e

    elapsed_time = time.monotonic() - start_time
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
        # ordered teardown with a bounded drain + error aggregation
        await shutdown_services(background_tasks, app_runner)


async def _cancel_tasks(tasks: list[asyncio.Task], timeout: float) -> None:
    """Cancel + bounded wait; re-cancel stragglers so teardown always runs.

    ``asyncio.wait`` alone returns still-running tasks in ``pending`` -- without
    the re-cancel a cancellation-ignoring task would skip every step below.
    """
    for t in tasks:
        t.cancel()
    if tasks:
        _done, pending = await asyncio.wait(tasks, timeout=timeout)
        for t in pending:
            t.cancel()
            logger.warning(f"Background task {t.get_name()} ignored cancellation; re-cancelled")


# Shutdown has to finish inside the container's stop grace period: SIGKILL
# mid-teardown skips the touch flush. The graceful steps share ONE budget
# instead of each taking its own generous timeout, but every teardown step
# still gets a floor: a spent budget must not turn db.close() into a 0 s
# timeout, which would record a failure that never happened and exit non-zero
# on a perfectly healthy stop.
#
# The bound is _SHUTDOWN_BUDGET_SECONDS + steps * _TEARDOWN_FLOOR_SECONDS, and
# it must fit the SMALLEST grace period we actually ship for, not compose's
# comfortable one: Heroku, Render and Kubernetes all SIGKILL at 30s by default
# and neither app.json nor render.yaml can raise it. A 40s budget x 2s floors
# gave a 50s worst case that could not fit any of them, so a stop could be
# killed before the touch flush ran -- the exact outcome this budget exists to
# prevent. The assert below makes that structural: retune the constants and the
# import fails rather than the deploy.
#
# docker-compose still gets 60s (stop_grace_period), which is comfortable.
_MIN_PAAS_GRACE_SECONDS = 30.0
_SHUTDOWN_BUDGET_SECONDS = 20.0
_TEARDOWN_STEP_SECONDS = 8.0
_TEARDOWN_FLOOR_SECONDS = 1.5
_TEARDOWN_STEP_COUNT = 5
_SHUTDOWN_WORST_CASE_SECONDS = (
    _SHUTDOWN_BUDGET_SECONDS + _TEARDOWN_STEP_COUNT * _TEARDOWN_FLOOR_SECONDS
)

assert _SHUTDOWN_WORST_CASE_SECONDS < _MIN_PAAS_GRACE_SECONDS, (
    f"shutdown can take {_SHUTDOWN_WORST_CASE_SECONDS:.1f}s but the PaaS manifests "
    f"only allow {_MIN_PAAS_GRACE_SECONDS:.0f}s -- a stop would be SIGKILLed before "
    "the touch flush and db.close() run"
)

# cleanup_clients is 10s PER CLIENT, so a fixed cap cuts a healthy stop as soon
# as there is more than one client -- recorded as an error, exit 1, and the PaaS
# restarts a container that was shutting down fine. Scale its cap instead.
_CLIENT_STOP_SECONDS = 10.0
_CLIENT_STOP_MAX_SECONDS = 60.0


def _client_stop_cap() -> float:
    """Ceiling for cleanup_clients: 10s per client, bounded.

    With a single client this is 10s -- more than the generic 8s step cap, and
    that difference is the whole point: the generic cap was cutting a healthy
    stop and reporting it as a failure. Still bounded so 40 clients cannot hold
    the shutdown open indefinitely.
    """
    return min(
        _CLIENT_STOP_MAX_SECONDS,
        max(1, len(multi_clients)) * _CLIENT_STOP_SECONDS,
    )


def _budget_left(deadline: float) -> float:
    """Seconds remaining in the shared shutdown budget, floored at 0."""
    return max(deadline - asyncio.get_running_loop().time(), 0.0)


def _step_budget(deadline: float) -> float:
    """Timeout for one shutdown step: what is left of the budget, capped per
    step but never below the floor."""
    return min(_TEARDOWN_STEP_SECONDS, max(_budget_left(deadline), _TEARDOWN_FLOOR_SECONDS))


def _teardown_plan() -> list[tuple[str, object, float | None]]:
    """The ordered teardown steps, with the per-step ceiling for each.

    Declarative so the ordering AND each step's cap are inspectable: the
    clients step needs a cap scaled by the client count, and a test that only
    watched the clock could not tell a correct cap from one that was never
    wired up (the scaled cap being far larger than any test's patience).
    Order matters: the touch flush must precede db.close().
    """
    return [
        ("rate limiter", rate_limiter.shutdown, None),
        ("touch buffer", drain_background_touch_tasks, None),
        ("shortener", close_shortener, None),
        # 10s per client, so the generic 8s ceiling cuts a healthy multi-client
        # stop and records a failure that never happened
        ("clients", cleanup_clients, _client_stop_cap()),
        ("database", db.close, None),
    ]


async def _safe_teardown_step(
    step,
    name: str,
    errors: list | None = None,
    deadline: float | None = None,
    *,
    cap: float | None = None,
):
    """Run one teardown step on its share of the budget.

    ``cap`` overrides the per-step ceiling for a step whose own work scales with
    something the budget cannot know -- cleanup_clients stops every client
    individually, so a fixed 8s cut a perfectly healthy multi-client stop and
    turned it into a recorded failure.
    """
    ceiling = _TEARDOWN_STEP_SECONDS if cap is None else cap
    if deadline is None:
        budget = ceiling
    else:
        budget = min(ceiling, max(_budget_left(deadline), _TEARDOWN_FLOOR_SECONDS))
    try:
        await asyncio.wait_for(step(), timeout=budget)
    except asyncio.CancelledError as e:
        # shutdown completeness wins over cancellation propagation: record
        # and continue with the remaining steps instead of aborting them
        logger.warning(f"{name} cleanup cancelled: {e}")
        if errors is not None:
            errors.append((name, e))
    except Exception as e:
        logger.error(f"Error during {name} cleanup: {e}", exc_info=True)
        if errors is not None:
            errors.append((name, e))


async def shutdown_services(background_tasks, app_runner) -> None:
    """Restart-marker-safe, bounded drain, aggregated errors."""
    print("   ▶ Shutting down services...")
    errors: list = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SHUTDOWN_BUDGET_SECONDS

    # 1. stop accepting new work
    for task in background_tasks:
        if not task.done():
            task.cancel()

    # one bounded wait for the whole batch (per-task waits would stack)
    if background_tasks:
        done, pending = await asyncio.wait(
            background_tasks, timeout=min(10.0, _budget_left(deadline))
        )
        for t in pending:
            t.cancel()
            logger.warning(f"Background task {t.get_name()} ignored cancellation; re-cancelled")
        for t in done:
            if t.cancelled():
                continue
            exc = t.exception()
            if exc is not None:
                # worker/index failures are fatal; sweeper/keepalive/token
                # blips must not force a container restart loop
                if t.get_name().startswith(("request_executor", "ensure_")):
                    errors.append((t.get_name(), exc))
                logger.error(f"Background task {t.get_name()} failed at shutdown: {exc}")

    # 2. stop accepting new HTTP before draining in-flight streams
    if app_runner is not None:
        try:
            await asyncio.wait_for(app_runner.cleanup(), timeout=_step_budget(deadline))
        except Exception as e:
            errors.append(("web server", e))
            logger.error(f"Error during web server cleanup: {e}")

    # 3. bounded drain: wait for in-flight streams, never past the budget
    drain_deadline = min(loop.time() + 30.0, deadline)
    while sum(work_loads.values()) > 0 and loop.time() < drain_deadline:
        await asyncio.sleep(0.25)
    remaining = sum(work_loads.values())
    if remaining:
        logger.warning(f"Drain deadline hit with {remaining} stream(s) still active.")

    # 4. ordered teardown
    for name, step, cap in _teardown_plan():
        await _safe_teardown_step(step, name, errors, deadline, cap=cap)
    if _budget_left(deadline) <= 0:
        logger.warning("Shutdown budget exhausted; teardown steps ran on the floor timeout.")
    if not errors:
        print("   ✓ Database connection closed")

    logger.info(f"Touch buffer final state: {touch_buffer_stats()}")

    # aggregate, log everything, exit non-zero when anything failed
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
    """Prune limiter bookkeeping every 5 minutes."""
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
    # Restrictive umask covers session keys before harden_session_files runs;
    # logs/ predates it, but log content is redacted.
    os.umask(0o077)
    try:
        asyncio.run(start_services())
    except KeyboardInterrupt:
        print("╔═══════════════════════════════════════════════════════════╗")
        print("║                   Bot stopped by user (CTRL+C)            ║")
        print("╚═══════════════════════════════════════════════════════════╝")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
