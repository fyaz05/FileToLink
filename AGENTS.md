# AGENTS.md — Thunder File-to-Link Bot

Python 3.13+ Telegram bot converting files to direct HTTP links. Uses Pyrofork, aiohttp, MongoDB, uvloop.

## Run

```bash
python -m Thunder          # Primary entry point
bash thunder.sh            # Updates from UPSTREAM_REPO, then runs the bot
python3 update.py          # Update only (rolls back if the new tree is unusable)
# update.py exits 0 when it cannot apply: no UPSTREAM_REPO, or no git/.git in a container
```

## Dependencies

```bash
pip install -r requirements.txt
# aiohttp, cloudscraper, Jinja2, pyrofork, pymongo, psutil, python-dotenv, speedtest-cli, tgcrypto-pyrofork
# uvloop (non-Windows/Cygwin only, requirements.txt marker); asyncio loop elsewhere
```

## Project Structure

```text
Thunder/
├── __init__.py              # __version__, StartTime
├── __main__.py              # Entry: start_services() via asyncio
├── vars.py                  # Configuration from env vars
├── bot/
│   ├── __init__.py          # StreamBot client, multi_clients, work_loads
│   ├── clients.py           # Multi-client management
│   └── plugins/
│       ├── admin.py         # Owner commands: /users /broadcast /status /stats /restart /log /authorize /deauthorize /listauth /ban /unban /shell /speedtest
│       ├── callbacks.py     # Inline keyboard handlers
│       ├── common.py        # User commands: /start /help /about /dc /ping
│       └── stream.py        # /link (groups), private/channel media handlers
├── server/
│   ├── __init__.py          # web_server() — creates aiohttp app with routes
│   ├── stream_routes.py     # HTTP streaming endpoints
│   └── exceptions.py        # Custom HTTP exceptions
├── utils/                   # 20 modules — see imports below
└── template/                # req.html (Jinja2)
```

## Docker

The image runs as unprivileged user `10001` and installs no build toolchain (all
dependencies provide wheels). `.dockerignore` excludes env files, session files,
virtualenvs, dev tools, and tests; supply `config.env` at runtime.

Two gotchas:
- A mounted `config.env` must be readable by uid `10001` (`chmod 644`; a
  root-only `600` fails). Do not `chown` it — that hands the file to a host-unknown
  uid you can no longer edit.
- Docker's `--env-file` passes values verbatim, so inline comments and quotes in
  `config_sample.env` corrupt variables (`PORT` becomes `8080 # Web server port`).
  Generate the file with `python-dotenv` outside the repo (see README), or mount `config.env`.

Self-update is skipped inside the image: there is no `git` and no `.git`, so
`UPSTREAM_REPO` has no effect there. Update a container by building a new image.

## Key Imports

```python
from Thunder.utils.logger import logger          # Async-safe QueueHandler logger, writes to Thunder/logs/bot.txt
from Thunder.utils.database import db             # AsyncMongoClient singleton
from Thunder.utils.rate_limiter import rate_limiter, request_executor, handle_rate_limited_request
from Thunder.utils.bot_utils import is_admin      # async def is_admin(cli, chat_id_val) -> bool — checks bot membership, NOT a decorator
from Thunder.utils.decorators import owner_only   # async guard function, not a decorator
from Thunder.vars import Var                      # All env config
```

## Code Conventions

- PEP 8, 4-space indent, 120-char lines
- Imports: stdlib → third-party → local
- All I/O is async; use `asyncio.sleep()` not `time.sleep()`
- Catch `FloodWait` from Telegram API with `await asyncio.sleep(e.value)`
- Log with `logger.error(..., exc_info=True)` for exceptions
- Admin access: `filters.user(Var.OWNER_ID)` on Pyrogram handlers (not `is_admin()`)
- Naming: PascalCase classes, snake_case functions/vars, UPPER_SNAKE_CASE constants

## Rate Limiting

Two-tier deque system in `rate_limiter.py`:
- Owners bypass queue entirely
- Authorized users → `priority_queue` (drained first)
- Regular users → `request_queue`
- `QueueFullError` raised on overflow

## Configuration

Copy `config_sample.env` → `config.env`. Required vars: `API_ID`, `API_HASH`, `BOT_TOKEN`, `BIN_CHANNEL`, `DATABASE_URL`.

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # ~20s; no network or Telegram credentials needed
python -m ruff check .       # config lives in pyproject.toml
```

Covers request parsing, slot conservation and truncation on the streaming
routes, the import cycle, database naming, /status before startup,
update.py preconditions and rollback, plugin handler registration, the CI
workflow's invariants, and .gitignore hygiene.

`pytest` and `ruff` both read `pyproject.toml`; there is no `pytest.ini`.

## Debugging

- Logs: Thunder/logs/bot.txt
- Health check: admin /status command, or GET /status on the web port