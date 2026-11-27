"""Startup cleanup must cover early failures, cancellation, and live tasks."""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def run_probe(source):
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=REPO,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLEANUP_OK" in result.stdout, result.stdout


def test_cleanup_cancels_tasks_and_closes_every_resource():
    run_probe(r'''
import asyncio
from types import SimpleNamespace
import Thunder.__main__ as entry

calls = []

class Runner:
    async def cleanup(self):
        calls.append("runner")

class Client:
    is_initialized = False
    is_connected = True

    async def stop(self):
        calls.append("primary")
        self.is_initialized = False
        self.is_connected = False

async def cleanup_clients():
    calls.append("clients")

async def shutdown():
    calls.append("limiter")

async def drain():
    calls.append("touches")

async def close():
    calls.append("database")

entry.rate_limiter.shutdown = shutdown
entry.cleanup_clients = cleanup_clients
entry.drain_background_touch_tasks = drain
entry.db.close = close
entry.StreamBot = Client()

async def worker():
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        calls.append("task")
        raise

async def main():
    task = asyncio.create_task(worker(), name="keepalive_task")
    await asyncio.sleep(0)
    await entry.cleanup_services({"app_runner": Runner(), "background_tasks": [task]})
    assert calls == ["runner", "limiter", "task", "clients", "primary", "touches", "database"], calls
    assert task.cancelled()
    print("CLEANUP_OK")

entry.LOOP.run_until_complete(main())
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
''')


def test_a_failed_primary_stop_retry_does_not_skip_database_cleanup():
    run_probe(r'''
import asyncio
from pyrogram.errors import FloodWait
import Thunder.__main__ as entry

calls = []

class Client:
    is_initialized = True

    async def stop(self):
        calls.append("stop")
        if calls.count("stop") == 1:
            raise FloodWait(value=0)
        raise RuntimeError("stop failed")

async def no_sleep(_seconds):
    return None

async def cleanup_clients():
    calls.append("clients")

async def shutdown():
    calls.append("limiter")

async def drain():
    calls.append("touches")

async def close():
    calls.append("database")

entry.StreamBot = Client()
entry.asyncio.sleep = no_sleep
entry.cleanup_clients = cleanup_clients
entry.rate_limiter.shutdown = shutdown
entry.drain_background_touch_tasks = drain
entry.db.close = close

entry.LOOP.run_until_complete(entry.cleanup_services({"app_runner": None, "background_tasks": []}))
assert calls == ["limiter", "clients", "stop", "stop", "stop", "touches", "database"], calls
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
print("CLEANUP_OK")
''')


def test_an_early_start_failure_still_closes_clients_and_database():
    run_probe(r'''
import asyncio
import Thunder.__main__ as entry

calls = []

class Client:
    is_initialized = False

    async def start(self):
        raise RuntimeError("telegram unavailable")

async def cleanup_clients():
    calls.append("clients")

async def shutdown():
    calls.append("limiter")

async def drain():
    calls.append("touches")

async def close():
    calls.append("database")

entry.CHECK_ONLY = False
entry.StreamBot = Client()
entry.cleanup_clients = cleanup_clients
entry.rate_limiter.shutdown = shutdown
entry.drain_background_touch_tasks = drain
entry.db.close = close

try:
    entry.LOOP.run_until_complete(entry.start_services())
except SystemExit as exc:
    assert exc.code == 1
else:
    raise AssertionError("startup should fail")

assert calls == ["limiter", "clients", "touches", "database"], calls
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
print("CLEANUP_OK")
''')


def test_cleanup_is_shielded_until_teardown_finishes_before_cancellation_propagates():
    run_probe(r'''
import asyncio
import Thunder.__main__ as entry

calls = []
started = asyncio.Event()
release = asyncio.Event()

class Runner:
    async def cleanup(self):
        started.set()
        await release.wait()
        calls.append("runner")

async def cleanup_clients():
    calls.append("clients")

async def shutdown():
    calls.append("limiter")

async def drain():
    calls.append("touches")

async def close():
    calls.append("database")

entry.StreamBot = type("Client", (), {"is_initialized": False, "is_connected": False})()
entry.cleanup_clients = cleanup_clients
entry.rate_limiter.shutdown = shutdown
entry.drain_background_touch_tasks = drain
entry.db.close = close

async def main():
    task = asyncio.create_task(entry.cleanup_services({
        "app_runner": Runner(), "background_tasks": [],
    }))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert "database" not in calls
    release.set()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("cleanup cancellation must propagate after safe teardown")
    assert calls == ["runner", "limiter", "clients", "touches", "database"], calls
    print("CLEANUP_OK")

entry.LOOP.run_until_complete(main())
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
''')


def test_cancel_and_drain_survives_repeated_keyboard_interrupts():
    run_probe(r'''
import asyncio
import Thunder.__main__ as entry

calls = []
real_loop = entry.LOOP

async def worker():
    try:
        await asyncio.Event().wait()
    finally:
        calls.append("cleanup")

async def setup():
    task = asyncio.create_task(worker())
    await asyncio.sleep(0)
    return task

task = real_loop.run_until_complete(setup())

class InterruptingLoop:
    def __init__(self):
        self.calls = 0

    def run_until_complete(self, future):
        self.calls += 1
        if self.calls == 1:
            raise KeyboardInterrupt
        return real_loop.run_until_complete(future)

proxy = InterruptingLoop()
entry.LOOP = proxy
entry._cancel_and_drain(task)
assert proxy.calls >= 2
assert task.done() and task.cancelled()
assert calls == ["cleanup"]
entry.LOOP = real_loop
from Thunder.utils.logger import listener
listener.stop()
real_loop.close()
print("CLEANUP_OK")
''')


def test_cancellation_during_preflight_runs_the_same_cleanup():
    run_probe(r'''
import asyncio
from types import SimpleNamespace
import Thunder.__main__ as entry

calls = []

class Client:
    is_initialized = True

    async def start(self):
        calls.append("start")

    async def get_me(self):
        return SimpleNamespace(username="thunder_bot", first_name="Thunder")

    async def stop(self):
        calls.append("primary")
        self.is_initialized = False

async def cleanup_clients():
    calls.append("clients")

async def verify(report):
    raise asyncio.CancelledError

async def shutdown():
    calls.append("limiter")

async def drain():
    calls.append("touches")

async def close():
    calls.append("database")

entry.CHECK_ONLY = False
entry.StreamBot = Client()
entry.preflight.verify_configuration = verify
entry.cleanup_clients = cleanup_clients
entry.rate_limiter.shutdown = shutdown
entry.drain_background_touch_tasks = drain
entry.db.close = close

async def main():
    task = asyncio.create_task(entry.start_services())
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("cancellation should propagate")
    assert calls == ["start", "limiter", "clients", "primary", "touches", "database"], calls
    print("CLEANUP_OK")

entry.LOOP.run_until_complete(main())
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
''')


def test_owner_alerts_always_use_the_primary_bot():
    run_probe(r'''
from pyrogram.errors import FloodWait
import Thunder.__main__ as entry

calls = []
sleeps = []

class Primary:
    async def send_message(self, chat_id, text):
        calls.append(("primary", chat_id, text))
        if len(calls) == 1:
            raise FloodWait(value=3)

class Secondary:
    async def send_message(self, chat_id, text):
        raise RuntimeError("secondary cannot initiate this chat")

async def no_sleep(seconds):
    sleeps.append(seconds)

entry.StreamBot = Primary()
entry.Var.OWNER_ID = 123
entry.asyncio.sleep = no_sleep
from Thunder.bot import multi_clients, work_loads
multi_clients.clear()
multi_clients.update({0: entry.StreamBot, 1: Secondary()})
work_loads.clear()
work_loads.update({0: 1, 1: 0})
entry.LOOP.run_until_complete(entry.notify_owner("alert"))
assert calls == [("primary", 123, "alert"), ("primary", 123, "alert")], calls
assert sleeps == [3]
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
print("CLEANUP_OK")
''')


def test_check_mode_retries_floodwait_like_normal_startup():
    run_probe(r'''
import asyncio
from types import SimpleNamespace
from pyrogram.errors import FloodWait
import Thunder.__main__ as entry

calls = {"start": 0}
sleeps = []

class Bot:
    async def start(self):
        calls["start"] += 1
        if calls["start"] == 1:
            raise FloodWait(value=3)

    async def get_me(self):
        return SimpleNamespace(username="bot")

async def verify(report):
    return None

async def initialize(report):
    return None

async def no_sleep(seconds):
    sleeps.append(seconds)

entry.StreamBot = Bot()
entry.preflight.verify_configuration = verify
entry.initialize_clients = initialize
entry.asyncio.sleep = no_sleep
try:
    entry.LOOP.run_until_complete(entry.check_configuration_only())
except SystemExit as exc:
    assert exc.code == 0
else:
    raise AssertionError("check mode should exit")
assert calls["start"] == 2
assert sleeps == [3]
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
print("CLEANUP_OK")
''')


def test_check_mode_reports_secondary_failures_without_primary_advice():
    run_probe(r'''
from types import SimpleNamespace
import Thunder.__main__ as entry

logs = []

class Bot:
    async def start(self):
        return None

    async def get_me(self):
        return SimpleNamespace(username="bot")

async def verify(report):
    return None

async def fail_secondaries(report):
    raise RuntimeError("secondary remained connected")

entry.StreamBot = Bot()
entry.preflight.verify_configuration = verify
entry.initialize_clients = fail_secondaries
entry.logger.info = lambda text: logs.append(text)
entry.logger.error = lambda *args, **kwargs: None
try:
    entry.LOOP.run_until_complete(entry.check_configuration_only())
except SystemExit as exc:
    assert exc.code == 1
else:
    raise AssertionError("check mode should fail")
text = "\n".join(logs)
assert "Secondary client initialization" in text
assert "Check BOT_TOKEN" not in text
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
print("CLEANUP_OK")
''')


def test_normal_startup_stops_before_secondaries_after_preflight_failure():
    run_probe(r'''
from types import SimpleNamespace
import Thunder.__main__ as entry

calls = []

class Bot:
    async def start(self):
        return None

    async def get_me(self):
        return SimpleNamespace(username="bot", first_name="Bot")

async def verify(report):
    report.add("fail", "database cannot write")

async def initialize(report):
    calls.append("secondaries")

entry.CHECK_ONLY = False
entry.StreamBot = Bot()
entry.print_banner = lambda: None
entry.preflight.verify_configuration = verify
entry.initialize_clients = initialize
entry.logger.info = lambda *args, **kwargs: None
entry.logger.error = lambda *args, **kwargs: None
try:
    entry.LOOP.run_until_complete(entry._start_services({"app_runner": None, "background_tasks": []}))
except SystemExit as exc:
    assert exc.code == 1
else:
    raise AssertionError("startup should fail")
assert calls == [], calls
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
print("CLEANUP_OK")
''')


def test_cleanup_lets_the_request_executor_drain_before_cancelling_it():
    run_probe(r'''
import asyncio
import Thunder.__main__ as entry

calls = []
shutdown_started = asyncio.Event()
release = asyncio.Event()

async def shutdown():
    calls.append("limiter")
    shutdown_started.set()

async def cleanup_clients():
    calls.append("clients")

async def drain():
    calls.append("touches")

async def close():
    calls.append("database")

async def executor():
    try:
        await release.wait()
        calls.append("executor")
    except asyncio.CancelledError:
        calls.append("cancelled")
        raise

entry.StreamBot = type("Client", (), {"is_initialized": False, "is_connected": False})()
entry.rate_limiter.shutdown = shutdown
entry.cleanup_clients = cleanup_clients
entry.drain_background_touch_tasks = drain
entry.db.close = close

async def main():
    task = asyncio.create_task(executor(), name="request_executor_task")
    cleanup = asyncio.create_task(entry.cleanup_services({
        "app_runner": None, "background_tasks": [task],
    }))
    await shutdown_started.wait()
    await asyncio.sleep(0)
    assert not task.cancelled()
    assert "executor" not in calls
    release.set()
    await cleanup
    assert calls == ["limiter", "executor", "clients", "touches", "database"], calls
    print("CLEANUP_OK")

entry.LOOP.run_until_complete(main())
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
''')


def test_cleanup_finishes_before_propagating_a_repeated_keyboard_interrupt():
    run_probe(r'''
import Thunder.__main__ as entry

calls = []

async def shutdown():
    calls.append("limiter")

async def cleanup_clients():
    calls.append("clients")

async def drain():
    calls.append("touches")

async def close():
    calls.append("database")

entry.StreamBot = type("Client", (), {"is_initialized": False, "is_connected": False})()
entry.rate_limiter.shutdown = shutdown
entry.cleanup_clients = cleanup_clients
entry.drain_background_touch_tasks = drain
entry.db.close = close
real_shield = entry.asyncio.shield
state = {"calls": 0}

def interrupt_once(task):
    state["calls"] += 1
    if state["calls"] == 1:
        raise KeyboardInterrupt
    return real_shield(task)

entry.asyncio.shield = interrupt_once
try:
    entry.LOOP.run_until_complete(entry.cleanup_services({
        "app_runner": None, "background_tasks": [],
    }))
except KeyboardInterrupt:
    pass
else:
    raise AssertionError("the repeated interrupt should propagate after cleanup")
assert calls == ["limiter", "clients", "touches", "database"], calls
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
print("CLEANUP_OK")
''')


def test_keyboard_interrupt_during_executor_drain_finishes_cleanup():
    run_probe(r'''
import asyncio
import Thunder.__main__ as entry

calls = []

async def shutdown():
    calls.append("limiter")

async def cleanup_clients():
    calls.append("clients")

async def drain():
    calls.append("touches")

async def close():
    calls.append("database")

async def executor():
    await asyncio.Event().wait()

entry.StreamBot = type("Client", (), {"is_initialized": False, "is_connected": False})()
entry.rate_limiter.shutdown = shutdown
entry.cleanup_clients = cleanup_clients
entry.drain_background_touch_tasks = drain
entry.db.close = close
real_wait = entry.asyncio.wait
state = {"calls": 0}

def interrupt_once(tasks, *args, **kwargs):
    state["calls"] += 1
    if state["calls"] == 1:
        raise KeyboardInterrupt
    return real_wait(tasks, *args, **kwargs)

entry.asyncio.wait = interrupt_once

async def main():
    task = asyncio.create_task(executor(), name="request_executor_task")
    await entry.cleanup_services({
        "app_runner": None, "background_tasks": [task],
    })

try:
    entry.LOOP.run_until_complete(main())
except KeyboardInterrupt:
    pass
else:
    raise AssertionError("the interrupt should propagate after cleanup")
assert calls == ["limiter", "clients", "touches", "database"], calls
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
print("CLEANUP_OK")
''')


def test_request_executor_cancellation_has_a_hard_cleanup_bound():
    run_probe(r'''
import asyncio
import Thunder.__main__ as entry

calls = []
release = asyncio.Event()

async def shutdown():
    calls.append("limiter")

async def cleanup_clients():
    calls.append("clients")

async def drain():
    calls.append("touches")

async def close():
    calls.append("database")

async def executor():
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        calls.append("cancelled")
        await release.wait()
        calls.append("released")

entry.REQUEST_DRAIN_SECONDS = 0.01
entry.REQUEST_CANCEL_SECONDS = 0.01
entry.StreamBot = type("Client", (), {"is_initialized": False, "is_connected": False})()
entry.rate_limiter.shutdown = shutdown
entry.cleanup_clients = cleanup_clients
entry.drain_background_touch_tasks = drain
entry.db.close = close

async def main():
    task = asyncio.create_task(executor(), name="request_executor_task")
    cleanup = asyncio.create_task(entry.cleanup_services({
        "app_runner": None, "background_tasks": [task],
    }))
    done, _pending = await asyncio.wait({cleanup}, timeout=1)
    if cleanup not in done:
        release.set()
        await cleanup
        raise AssertionError("cleanup blocked on a cancellation-suppressing task")
    await cleanup
    assert "database" in calls
    assert not task.done()
    release.set()
    await task
    print("CLEANUP_OK")

entry.LOOP.run_until_complete(main())
from Thunder.utils.logger import listener
listener.stop()
entry.LOOP.close()
''')
