# AGENTS.md — Thunder File-to-Link Bot

Python 3.13 Telegram bot converting files to direct HTTP links. Uses Pyrofork, aiohttp, MongoDB, uvloop.

This file is the agent/dev handbook: conventions here are enforced by CI
(`quality.yml`) and by tests. Update it in the same PR that changes behavior
or configuration.

## Run

```bash
python -m Thunder          # Primary entry point
bash thunder.sh            # best-effort self-update (shell-free) + python3 -m Thunder
```

## Dependencies

Managed in `pyproject.toml`, exported to `requirements.txt` (8 direct deps,
all exact-pinned; the CI dependency-count gate fails beyond 8).
`uv.lock` pins the full transitive graph with hashes — regenerate it with
`uv lock` whenever `pyproject.toml` changes (CI fails if it drifts).
`requirements.lock` is the hash-pinned full-graph export that the Dockerfile
installs with `--require-hashes`; CI fails if it drifts from `uv.lock`:

```bash
uv sync --frozen      # reproducible env from the lockfile
pip install -r requirements.txt        # direct pins (human installs)
pip install --require-hashes -r requirements.lock  # what Docker ships
# aiohttp, pyrofork, tgcrypto-pyrofork, pymongo, Jinja2, python-dotenv, psutil, uvloop
```

`cloudscraper` and `speedtest-cli` were removed (unmaintained / archived).

## Development

```bash
make format      # ruff autofix + format
make lint        # ruff + mypy (blocking: 0 errors expected)
make test        # unit tier (hermetic: no network, no Mongo)
make audit       # pip-audit + bandit + vulture + dependency-count
```

## Test tiers

- **Unit** (default, every PR): `pytest -m unit --cov=Thunder --cov-report=term-missing --cov-fail-under=35`
  — pure logic only.
- **Integration** (opt-in, needs Docker): `TEST_INTEGRATION=1 uv run pytest -m integration`
  — testcontainers MongoDB (`testcontainers[mongodb]`, declared in the dev
  group); ingest-claim locks, token-activation atomicity.
- Characterization tests pin behavior before refactors; update them
  deliberately inside the PR that changes behavior.

## Project Structure

```text
Thunder/
├── __init__.py              # __version__, StartTime
├── __main__.py              # Entry: start_services(), executor pool, sweepers, M13 shutdown
├── vars.py                  # Config: .env layering, collects ALL validation errors, named failures
├── bot/
│   ├── __init__.py          # StreamBot client, multi_clients, work_loads
│   ├── registry.py          # Single command registry → menu / help / AGENTS.md (M1)
│   ├── clients.py           # Multi-client management + session-file chmod 0600 (L5)
│   └── plugins/
│       ├── admin.py         # Owner commands (speedtest removed; /log redacted; /shell opt-in)
│       ├── callbacks.py     # Inline keyboard handlers + panic-isolation guard (M11)
│       ├── common.py        # User commands: /start /help /about /dc /ping
│       └── stream.py        # /link (groups), private/channel media, batch worker pool (M4b)
├── server/
│   ├── __init__.py          # web_server() + access-log middleware with hashed tokens (H10)
│   ├── stream_routes.py     # HTTP endpoints: /health /status /activate/{token} /f /watch
│   └── exceptions.py        # Custom exceptions
├── utils/                   # safe_call, flag_cache, media_types are new foundational modules
└── template/                # req.html (typed player: video/audio/image/other)
```

## Commands (generated from Thunder/bot/registry.py — keep in sync)

| Command | Access | Description |
|---|---|---|
| `/start` | user | Start the bot and get a welcome message |
| `/help` | user | Show help and usage instructions |
| `/link` | group | Generate a direct link for a file or batch |
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
| `/shell` | owner | Execute a shell command (requires `ENABLE_SHELL=True`) |
| `/authorize` | owner | Grant permanent access to a user |
| `/deauthorize` | owner | Remove permanent access from a user |
| `/listauth` | owner | List all authorized users |

Owner-only commands are hidden from the Telegram command menu.

## Key Imports

```python
from Thunder.utils.logger import logger          # leveled logger; LOG_LEVEL/LOG_FORMAT envs
from Thunder.utils.logger import redact_secrets  # shared token/Mongo-URI redaction (H10)
from Thunder.utils.database import db             # AsyncMongoClient singleton, timeoutMS=5000
from Thunder.utils.safe_call import tg_call      # FloodWait-safe RPC helper (H4a) + wrappers
from Thunder.utils.flag_cache import flags       # TTL+LRU flag cache (H7)
from Thunder.utils.rate_limiter import rate_limiter, handle_rate_limited_request, start_executors
from Thunder.utils.decorators import preflight   # unified gate chain (M12)
from Thunder.vars import Var                      # All env config
```

## Code Conventions

- PEP 8, 4-space indent; ruff (E,F,W,I,UP,B,SIM) enforced in CI
- Imports: stdlib → third-party → local (ruff `I` sorts them)
- All I/O is async; blocking calls go through `asyncio.to_thread`
- **Never write `try/except FloodWait` pairs**: call `tg_call(fn, *args,
  retries=1, timeout=...)` or a wrapper (`reply_safe`, `send_safe`,
  `edit_safe`, `delete_safe`, `answer_safe`). The only allowed inline
  FloodWait loops are the streaming/pool paths in `custom_dl.py` and the
  ingest retry loop in `canonical_files.py`.
- Every external call has a budget: Mongo `timeoutMS=5000`, Telegram RPC
  `TG_RPC_TIMEOUT_SECONDS` (file transfers unbounded by default), shortener
  and keepalive 10 s
- `html.escape()` every user-controlled string interpolated into HTML
  messages (M7); user-facing link/welcome/help surfaces are HTML now
- Fail-closed: flag lookups deny on DB errors with `MSG_ERROR_TEMP` (H7)
- Admin access: `filters.user(Var.OWNER_ID)` on Pyrogram handlers
- Naming: PascalCase classes, snake_case functions/vars, UPPER_SNAKE_CASE constants

## Access gates (M12 preflight chain — documented ordering)

`banned → private-mode → token-activation → force-sub → shortener-status`

- Owner bypasses everything; authorized users bypass all but the ban check.
- `/start` runs only `banned + private-mode` so the activation flow stays reachable.
- `PRIVATE_MODE=True` restricts the whole bot to owner + authorized users.
- Messages with no attributable sender (`from_user is None`: channel posts,
  anonymous admins) are DENIED by the private-mode and token gates —
  fail-closed, never bypass.
- Adding a new gate = one entry in `PREFLIGHT_GATES` + a row above.

## Rate Limiting

Two-tier deque system in `rate_limiter.py` (queue/wait-estimate UX is a
protected behavior — internals may change, the UX may not):

- Owners bypass; authorized users → `priority_queue` (drained first)
- Sliding window is charged at **execution** time (charge-at-exec, H6b)
- FloodWait inside a worker requeues the request with an attempt counter
  (max 5) instead of stalling the pool
- Global RPS token-bucket breaker sheds bursts (burst = 2× rate, H6c)
- Bookkeeping is bounded + swept every 5 min (H6a)

## URL families

- Canonical: `/f/<32-hex>/<name>` (new uploads, L4) and `/watch/f/<32-hex>/<name>`
- Legacy: `/watch/<6-char-hash><id>/<name>` — still valid; controlled by
  `ENABLE_LEGACY_LINKS` (default on; off → 410). Legacy pages are cached.
- Both 20- and 32-hex canonical hashes validate side-by-side forever.

## Configuration

Copy `config_sample.env` → `config.env`. Required: `API_ID`, `API_HASH`,
`BOT_TOKEN`, `BIN_CHANNEL`, `OWNER_ID` (boot refuses without it),
`DATABASE_URL`. Optional local overrides go in `config.env.local`.
Every new env var ships with a safe default and an annotated
`config_sample.env` entry in the same PR.

## Debugging

- Logs: `Thunder/logs/bot.txt` (rotating 10 MiB × 5; `/log` uploads are redacted)
- Liveness: `GET /health` (no dependencies touched); keepalive targets it
- Runtime status: `GET /status` (`Cache-Control: no-store`, includes DC id,
  inflight counts, touch-buffer stats) and admin `/stats` (limiter occupancy)
- CI is the quality bar: ruff, mypy, pytest, pip-audit, bandit, vulture,
  dependency-count — all must be green
