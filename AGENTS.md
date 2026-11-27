# AGENTS.md — Thunder File-to-Link Bot

Telegram file-to-link bot using Python 3.15, wzgram, aiohttp, and MongoDB.

## Run

```bash
python -m Thunder
python -m Thunder --check
bash thunder.sh
python3 update.py
```

Add `--verbose` to either Python command for full check output.

`update.py` exits `0` when no update applies; nonzero means it tried and failed.

## Structure

```text
Thunder/
├── __main__.py        # entry point and service lifecycle
├── vars.py            # config; raises one ConfigError
├── bot/               # StreamBot, multi_clients, plugins/
├── server/            # aiohttp routes
├── utils/             # helpers
└── template/          # player page and assets
```

Plugins: `admin.py`, `common.py`, `stream.py`, `callbacks.py`.

## Startup validation

1. `utils/config_values.py` records every parse problem; `vars.py` raises one `ConfigError`.
2. `utils/preflight.py` checks Telegram/Mongo token, channel rights, database writes, owner, force channel, and shortener. Definite failures stop startup; transient failures warn. The public URL is checked after the server binds.
3. `utils/channel_watch.py` reports access transitions every `CHANNEL_WATCH_INTERVAL` seconds; `0` disables it.

`utils/public_host.py` resolves `FQDN` or platform variables and reports missing-host/port drift.

Independent checks must stay concurrent. Only the channel check waits for a stored message ID. Use `gather(..., return_exceptions=True)` so one failure does not discard other results.

`/doctor` reruns stage 2, the public-URL probe, and secondary-client checks. `--check` skips the public-URL probe because it serves nothing. `/status` only reads cached counts.

## Gotchas

- Create the event loop before importing `Thunder.bot`; wzgram binds clients at creation.
- `db` is a `Database` wrapper, not the raw Mongo client.
- `is_admin()` and `owner_only()` are async functions. Use `filters.user(Var.OWNER_ID)` on handlers.
- A failed channel probe is not proof of lost access.

## Docker

Runs as uid `10001` without a toolchain. `.dockerignore` excludes env files and tests.

- Mounted `config.env` must be readable by dedicated uid/gid `10001`, never world-readable. Use `chmod 600` plus `setfacl -m u:10001:r`, or `chgrp 10001` plus `chmod 640`. With shared IDs or rootless/`userns-remap`, use the environment-file alternative or mapped host identity.
- Do not use `--env-file` with inline comments; mount `config.env`.
- Self-update is skipped because the image has no `git`.

## Conventions

- PEP 8, 4 spaces, 120 columns; stdlib → third-party → local imports.
- Async I/O only; use `asyncio.sleep()`.
- Handle `FloodWait` with `await asyncio.sleep(e.value)`.
- Log exceptions with `logger.error(..., exc_info=True)`.
- Keep comments, docstrings, logs, errors, and docs lean: explain only non-obvious constraints and actionable fixes; omit narration, history, and restatements of code.
- Owners bypass rate limits; authorized users use `priority_queue`, others `request_queue`.

## Testing

```bash
PYYAML_FORCE_LIBYAML=0 pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
```

PyYAML has no cp315 wheel. On Windows set `$env:PYYAML_FORCE_LIBYAML = "0"` first.

There is intentionally no `uv.lock`; floating requirements let CI catch bad releases. Windows/3.14 without `wzgram`/`warpcrypto` fails environmentally.

Regression tests should fail against the old code. Logs: `Thunder/logs/bot.txt`. Health: `/status`.
