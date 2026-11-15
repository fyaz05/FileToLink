"""Importing the plugins must actually register command handlers.

The four files in Thunder/bot/plugins/ are loaded by path at startup, and each
registers its handlers as a side effect of being imported. Two failures are
invisible to an ordinary import check:

* a plugin raises while importing -- import_plugins logs it and carries on, so
  the bot boots with that plugin's commands silently missing;
* a plugin imports fine but no longer registers anything, because its
  decorators were dropped. From the outside those look identical.

Both need the loop to run: Dispatcher.add_handler only *schedules* registration,
so counting handlers synchronously always reports the conversation handler alone.

The check runs in a subprocess for the same reason Thunder starts the way it
does: importing Thunder.__main__ installs the uvloop policy and sets a current
event loop, and neither belongs in the test process. The subprocess also gets
the production import order, where Thunder.__main__ comes before Thunder.bot so
the dispatcher binds to the loop the app actually runs.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "Thunder" / "bot" / "plugins"
MARKER = "REGISTRATION_RESULT="

# Var validates at import and raises without these, and config.env is absent in
# CI. Set them explicitly rather than inheriting a developer's real config,
# which would hide a missing requirement.
PLACEHOLDER_ENV = {
    "API_ID": "123456",
    "API_HASH": "0" * 32,
    "BOT_TOKEN": "123456:AA" + "x" * 30,
    "BIN_CHANNEL": "-1001234567890",
    "OWNER_ID": "1",
    "DATABASE_URL": "mongodb://localhost:27017/thunder_test",
}

PROBE = f'''
import asyncio, json, sys
sys.path.insert(0, ".")

# Production order: __main__ creates the loop, then importing Thunder.bot binds
# the dispatcher to it.
import Thunder.__main__ as entry
from Thunder.bot import StreamBot
from Thunder.utils.commands import get_commands


def count(dispatcher):
    return sum(len(group) for group in dispatcher.groups.values())


def commands_in(filt, depth=0):
    found = set()
    if filt is None or depth > 8:
        return found
    found.update(getattr(filt, "commands", None) or ())
    found |= commands_in(getattr(filt, "base", None), depth + 1)
    found |= commands_in(getattr(filt, "other", None), depth + 1)
    for extra in getattr(filt, "filters", None) or ():
        found |= commands_in(extra, depth + 1)
    return found


async def main():
    dispatcher = StreamBot.dispatcher
    before = count(dispatcher)
    imported = await entry.import_plugins()

    # One task per handler, plus the locks they take; wait for the count to stop
    # moving rather than guessing a number of ticks.
    stable = 0
    previous = -1
    for _ in range(500):
        await asyncio.sleep(0)
        now = count(dispatcher)
        stable = stable + 1 if now == previous else 0
        previous = now
        if stable >= 3:
            break

    registered = set()
    for group in dispatcher.groups.values():
        for handler in group:
            registered |= commands_in(getattr(handler, "filters", None))

    advertised = {{command.command for command in get_commands()}}
    print("{MARKER}" + json.dumps({{
        "imported": imported,
        "handlers_before": before,
        "handlers_after": count(dispatcher),
        "advertised": sorted(advertised),
        "missing": sorted(advertised - registered),
    }}))


entry.LOOP.run_until_complete(main())
'''


def run_probe():
    env = {**os.environ, **PLACEHOLDER_ENV}
    result = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO, env=env,
                            capture_output=True, timeout=600,
                            encoding="utf-8", errors="replace")
    assert result.returncode == 0, (
        "the plugin import crashed:\n" + (result.stderr or result.stdout)[-2000:]
    )
    line = next((ln for ln in (result.stdout or "").splitlines() if ln.startswith(MARKER)), None)
    assert line, "the probe produced no result:\n" + (result.stdout or result.stderr)[-2000:]
    return json.loads(line[len(MARKER):])


def test_importing_the_plugins_registers_every_advertised_command():
    expected_plugins = len(list(PLUGINS_DIR.glob("*.py")))
    assert expected_plugins, "no plugin files found, so this test proves nothing"

    outcome = run_probe()

    assert outcome["imported"] == expected_plugins, (
        f"only {outcome['imported']} of {expected_plugins} plugins imported; a "
        "failure is logged and the bot then boots with that plugin's commands "
        "silently missing"
    )

    registered = outcome["handlers_after"] - outcome["handlers_before"]
    assert registered > 0, "the plugins registered no handlers at all"

    assert outcome["advertised"], "no commands are advertised, so this proves nothing"
    assert outcome["missing"] == [], (
        f"advertised to Telegram but unhandled: {outcome['missing']}"
    )
