# AGENTS.md — Thunder File-to-Link Bot

Telegram bot converting files to direct HTTP links. Python 3.15, wzgram, aiohttp, MongoDB.

## Run

```bash
python -m Thunder [--check] [--verbose]   # --check runs the configuration checks and exits without serving
bash thunder.sh                           # update, then run
python3 update.py                         # update only; rolls back if unusable
```

`update.py` exits **0** when it cannot apply (no `UPSTREAM_REPO`, no `.git`), so a
nonzero exit means it tried and failed.

## Structure

```text
Thunder/
├── __init__.py        # __version__, StartTime
├── __main__.py        # entry: flags, uvloop, start_services()
├── vars.py            # config; raises ConfigError for the whole set
├── bot/               # StreamBot client, multi_clients, plugins/
├── server/            # aiohttp app, stream routes
├── utils/             # 24 modules
└── template/          # req.html (MiniJinja), player.css/js
```

Plugins: `admin.py` (owner commands), `common.py` (`/start /help /about /dc /ping`),
`stream.py` (`/link`, media handlers), `callbacks.py` (inline keyboards).

## Startup validation

Three stages; keep them separate.

1. `utils/config_values.py` parses env and **records every problem**, so one
   restart reports them all. `vars.py` raises one `ConfigError` after. Values
   with a single sane reading are corrected and noted (FQDN scheme/slash,
   quoted booleans, comma-separated `BANNED_CHANNELS`).
2. `utils/preflight.py` checks what only Telegram/Mongo can answer: token,
   channel membership **and posting right**, DB reachability **and writability**,
   owner, force channel, shortener, public URL. Definite failures stop startup;
   transient ones (timeout, `FloodWait`) only warn.
3. `utils/channel_watch.py` rechecks the channel every `CHANNEL_WATCH_INTERVAL`
   seconds after boot (`0` disables) and messages the owner once per transition.

`utils/public_host.py` resolves the host links are built from: `FQDN`, or the
platform variable when unset (`KOYEB_PUBLIC_DOMAIN`, `RENDER_EXTERNAL_URL`,
`RAILWAY_PUBLIC_DOMAIN`, `HEROKU_APP_NAME`, `FLY_APP_NAME`, `WEBSITE_HOSTNAME`),
normalised with FQDN rules. Its `describe_missing_host`/`describe_ignored_port`
findings are part of the stage-2 report.

Live checks run concurrently and **must stay that way**; only the channel check
waits (it needs a message id first). `gather(..., return_exceptions=True)` is
deliberate: one bad check must not discard the others.

`/doctor` re-runs stage 2 from Telegram (the only route with no shell). Neither
`--check` nor `/doctor` runs during a normal request: `/status` reads the cached
startup result.

Parsing is strict: `WORKERS=0`, `SLEEP_THRESHOLD<0`, `PING_INTERVAL<=0` are
rejected by the parser (`vars.py` raises one `ConfigError` naming them) instead
of crashing or busy-looping later.

## Gotchas

- **Import order:** wzgram binds a client to the loop current at creation, and
  `Thunder.bot` creates one at import. `__main__` installs uvloop and creates the
  loop *before* importing the package. Do not reorder.
- `db` is a `Database` wrapper (`utils/database.py`), not the raw Mongo client.
- `is_admin(cli, chat_id)` and `owner_only(client, update)` are **async functions**,
  not decorators. Use `filters.user(Var.OWNER_ID)` on handlers, not `is_admin()`.
- A failed channel probe is not proof of lost access. Do not turn it into an alert.

## Docker

Runs as uid `10001`, no toolchain. `.dockerignore` excludes env files and tests.

- A mounted `config.env` must be readable by `10001` (`chmod 644`). Do **not**
  `chown` it — you lose edit access.
- `--env-file` passes values verbatim, so inline comments corrupt them. Mount
  `config.env` instead.
- No `git` in the image, so self-update is skipped there.

## Conventions

- PEP 8, 4-space, 120 cols. stdlib → third-party → local imports.
- All I/O async. `asyncio.sleep()`, never `time.sleep()`.
- Catch `FloodWait` with `await asyncio.sleep(e.value)`.
- `logger.error(..., exc_info=True)` for exceptions.
- Rate limiting: owners bypass, authorized users → `priority_queue`, others →
  `request_queue`, `QueueFullError` on overflow.

## Testing

```bash
pip install -r requirements-dev.txt   # needs PYYAML_FORCE_LIBYAML=0 on 3.15
python -m pytest -q                   # ~20s Linux, ~45s Windows
python -m ruff check .
```

PyYAML has no `cp315` wheel, so set the env var first (`$env:PYYAML_FORCE_LIBYAML = "0"`
on Windows). Use `--no-cache-dir` to verify a genuinely clean install.

`uv` is optional (`uv venv --seed .venv --python 3.15`). There is deliberately **no
`uv.lock`**: `requirements.txt` floats so CI notices bad upstream releases.

On Windows/3.14 without `wzgram`/`warpcrypto` installed (no cp314 wheel), the
`update.py` tests fail with `ModuleNotFoundError` — environmental, not a code bug.

Coverage worth knowing before changing anything: streaming routes (ranges, slot
conservation), config parsing, preflight against fakes built from real
`pyrogram.types`, update/rollback paths, CI workflow invariants. A bug fix's
tests should fail against the old code.

Logs: `Thunder/logs/bot.txt`. Health: `/status` or `GET /status`.
