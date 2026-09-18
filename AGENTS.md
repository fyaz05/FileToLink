# AGENTS.md — Thunder File-to-Link Bot

Python 3.13 Telegram bot: files → direct HTTP links (Pyrofork, aiohttp, MongoDB, uvloop).
Enforced by CI (`quality.yml`) and tests — update in the same PR that changes behavior/config.

## Run

```bash
python -m Thunder          # primary entry point
bash thunder.sh            # best-effort self-update (shell-free) + python3 -m Thunder
```

## Dependencies

`pyproject.toml` owns 8 exact-pinned direct deps (CI fails beyond 8);
`uv.lock` pins the transitive graph (regenerate with `uv lock` on change);
`requirements.lock` is the hashed export Docker installs with `--require-hashes`.
CI fails if either drifts:

```bash
uv sync --frozen      # reproducible env
pip install -r requirements.txt                  # human installs
pip install --require-hashes -r requirements.lock  # what Docker ships
# aiohttp, pyrofork, tgcrypto-pyrofork, pymongo, Jinja2, python-dotenv, psutil, uvloop
```

## Development

```bash
make format      # ruff autofix + format
make lint        # ruff + mypy (blocking: 0 errors expected)
make test        # unit tier (hermetic: no network, no Mongo)
make audit       # pip-audit + bandit + vulture + dependency-count
```

## Test tiers

- **Unit** (every PR): `uv run pytest -m unit --cov=Thunder --cov-report=term-missing --cov-fail-under=35`
- **Integration** (opt-in, Docker): `TEST_INTEGRATION=1 uv run pytest -m integration` (testcontainers MongoDB, dev group)
- Characterization tests pin behavior; update deliberately in the behavior PR.

## Project Structure

```text
Thunder/
├── __init__.py       # __version__, StartTime
├── __main__.py       # start_services(), executor pool, sweepers, M13 shutdown
├── vars.py           # .env layering, collects ALL validation errors
├── bot/
│   ├── __init__.py   # StreamBot, multi_clients, work_loads
│   ├── registry.py   # command registry → menu / help / AGENTS.md (M1)
│   ├── clients.py    # multi-client + session chmod 0600 (L5)
│   └── plugins/      # admin (owner) / callbacks (M11) / common (user) / stream (/link, M4b)
├── server/           # __init__ (access log, hashed tokens H10) / stream_routes (/health /status /activate /f /watch) / exceptions
├── utils/            # safe_call, flag_cache, media_types + rate_limiter, decorators, tokens, ...
└── template/         # req.html (video/audio/image/other player)
```

## Commands (from Thunder/bot/registry.py — keep in sync)

| Command | Access | Description |
|---|---|---|
| `/start` | user | Start the bot and get a welcome message |
| `/help` | user | Show help and usage instructions |
| `/link` | group | (Group) Generate a direct link for a file or batch |
| `/dc` | user | Retrieve the data center (DC) information of a user or file |
| `/ping` | user | Check the bot's status and response time |
| `/about` | user | Get information about the bot |
| `/users` | owner | Show the total number of users |
| `/status` | owner | View bot details and current workload |
| `/stats` | owner | View usage statistics and resource consumption |
| `/broadcast` | owner | Send a message to all users |
| `/ban` | owner | Ban a user |
| `/unban` | owner | Unban a user |
| `/log` | owner | Send redacted bot logs |
| `/restart` | owner | Update and restart the bot |
| `/shell` | owner | Execute a shell command (requires `ENABLE_SHELL`) |
| `/authorize` | owner | Grant permanent access to a user |
| `/deauthorize` | owner | Remove permanent access from a user |
| `/listauth` | owner | List all authorized users |

Owner-only commands are hidden from the Telegram command menu.

## Key Imports

```python
from Thunder.utils.logger import logger, redact_secrets  # leveled log + token/Mongo redaction (H10)
from Thunder.utils.database import db             # AsyncMongoClient singleton, timeoutMS=5000
from Thunder.utils.safe_call import tg_call      # FloodWait-safe RPC + wrappers (H4a)
from Thunder.utils.flag_cache import flags       # TTL+LRU flag cache (H7)
from Thunder.utils.rate_limiter import rate_limiter, handle_rate_limited_request, start_executors
from Thunder.utils.decorators import preflight   # gate chain (M12)
from Thunder.vars import Var                      # all env config
```

## Code Conventions

- PEP 8, 4-space indent; ruff (E,F,W,I,UP,B,SIM) in CI
- Import order: stdlib → third-party → local; all I/O async (`asyncio.to_thread` for blocking)
- **Never `try/except FloodWait`**: use `tg_call(...)` / `reply_safe` / `send_safe` / `edit_safe` / `delete_safe` / `answer_safe`. Allowed inline: `custom_dl.py` streaming/resume, `rate_limiter.py` worker-requeue, `broadcast.py` classify-only catch.
- Budgets: Mongo `timeoutMS=5000`, TG RPC `TG_RPC_TIMEOUT_SECONDS` (transfers unbounded), shortener/keepalive 10 s
- `html.escape()` all user strings in HTML (M7); fail-closed deny with `MSG_ERROR_TEMP` (H7)
- Owner handlers: `filters.user(Var.OWNER_ID)`; PascalCase / snake_case / UPPER_SNAKE_CASE

## Access gates (M12 order: banned → private-mode → token; force-sub where applicable; shortener-status is routing, not a gate)

- Owner bypasses everything, including force-sub; authorized users bypass private-mode + token, but not ban or force-sub.
- `/start` runs only `banned + private-mode` (activation stays reachable).
- `PRIVATE_MODE=True` restricts the whole bot to owner + authorized users.
- No-sender messages (channel posts, anonymous admins) are DENIED wherever a gate is active — fail-closed, never bypass.
- New gate = one `PREFLIGHT_GATES` entry + a row above.

## Rate Limiting

- Two-tier deque (`priority_queue` first); queue/wait-estimate UX is protected.
- Charge-at-exec (H6b); FloodWait requeues (max 5); RPS breaker burst 2×, dry requeues (H6c); bounded + swept 5 min (H6a).

## URL families

- Canonical `/f/<32-hex>/<name>`, `/watch/f/<32-hex>/<name>` (L4); legacy `/watch/<6-char-hash><id>/<name>` valid, `ENABLE_LEGACY_LINKS` off → 410 (default on). 20- and 32-hex validate side-by-side forever; legacy pages cached.

## Configuration

Copy `config_sample.env` → `config.env` (+ optional `config.env.local` overrides).
Required: `API_ID`, `API_HASH`, `BOT_TOKEN`, `BIN_CHANNEL`, `OWNER_ID` (boot refuses), `DATABASE_URL`.
New env vars ship a safe default + annotated sample entry in the same PR.

## Debugging

- Logs `Thunder/logs/bot.txt` (10 MiB × 5; `/log` redacted); `GET /health` (dep-free); `GET /status` (no-store: DC, inflight, touch stats); admin `/stats` (limiter).
- CI bar: ruff, mypy, pytest, pip-audit, bandit, vulture, dependency-count — all green.
