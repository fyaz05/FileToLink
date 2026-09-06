# AGENTS.md — Thunder File-to-Link Bot

Python 3.13+ Telegram bot converting files to direct HTTP links. Uses Pyrofork, aiohttp, MongoDB, uvloop.

## Run

```bash
python -m Thunder          # Primary entry point
bash thunder.sh            # Runs python3 update.py && python3 -m Thunder
```

## Dependencies

```bash
pip install -r requirements.txt
# aiohttp, cloudscraper, Jinja2, pyrofork, pymongo, psutil, python-dotenv, speedtest-cli, tgcrypto, uvloop==0.21.0
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
│       ├── admin.py         # Owner commands: /users /broadcast /status /stats /restart /log /authorize /deauthorize /ban /unban /shell /speedtest
│       ├── callbacks.py     # Inline keyboard handlers
│       ├── common.py        # User commands: /start /help /about /dc /ping
│       └── stream.py        # /link (groups), private/channel media handlers
├── server/
│   ├── __init__.py          # web_server() — creates aiohttp app with routes
│   ├── stream_routes.py     # HTTP streaming endpoints
│   └── exceptions.py        # Custom HTTP exceptions
├── utils/                   # 20 modules — see imports below
└── template/                # dl.html, req.html (Jinja2)
```

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

## Debugging

- Logs: `Thunder/logs/bot.txt`
- Health check: admin `/status` command
- No linting/formatting tools configured — follow conventions manually
- No formal test suite — verify via bot interaction and link streaming