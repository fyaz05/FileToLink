# AGENTS.md — Thunder File-to-Link Bot

Python 3.13 Telegram bot: files → direct HTTP links (Pyrofork, aiohttp, MongoDB, uvloop).
Enforced by CI (`quality.yml`) and tests — update in the same PR that changes behavior/config.

## Run

```bash
python -m Thunder          # primary entry point
bash thunder.sh            # best-effort self-update (shell-free) + python3 -m Thunder
```

## Dependencies

`pyproject.toml` owns 7 exact-pinned direct packages. The CI gate counts
*distinct* names, so a conditional platform entry for the same package
(pyrofork with and without the `speedup` extra) does not count twice.
`uv.lock` pins the transitive graph (regenerate with `uv lock` on change);
`requirements.lock` is the hashed export Docker installs with
`--require-hashes`. CI fails if either drifts:

```bash
uv sync --frozen      # reproducible env
pip install -r requirements.txt                  # human installs
pip install --require-hashes -r requirements.lock  # what Docker ships
# aiohttp, pyrofork[speedup], pymongo, Jinja2, python-dotenv, psutil, uvloop
```

`uvloop` and `tgcrypto` (pulled in by `pyrofork[speedup]`) ship no Windows
wheels, so that extra is requested only where `sys_platform != 'win32'`:
POSIX deploys keep both speedups, Windows dev hosts can install and run the
suite.

## Development

```bash
make format      # ruff autofix + format
make lint        # ruff + mypy (blocking: 0 errors expected)
make test        # unit tier (hermetic: no network, no Mongo)
make audit       # pip-audit + bandit + vulture + dependency-count
```

### The fast loop — read this before running the full tier

Importing `pyrofork` costs **~2.8 s** and happens on *every* pytest invocation,
so there is a hard floor of **~3.8 s per run** no matter how few tests you
select. The expensive part is running the wrong command, not the tool being
slow. Measured on a Windows host with the dev group synced:

| command | time | what it covers |
|---|---|---|
| `scripts/dev.sh gates` | 1.6 s | ruff + ruff-format + mypy, no pytest |
| `scripts/dev.sh file <path>` | 3.8 s | one test file, or one node with `::` |
| `scripts/dev.sh quick` | 9.8 s | the whole unit tier, no coverage |
| `scripts/dev.sh test` | 15.1 s | the whole tier + the coverage gate |
| `scripts/dev.sh all` | 18.3 s | gates + `test`, i.e. what CI runs |

Best of three on a Windows host with the dev group synced, at 479 unit tests.
Re-measure before trusting the absolute numbers — they drift with the test
count, and a table nobody updates is a table nobody reads. This table has now
been measured at 414, 451 and 479 tests; `quick` tracked 6.8 s → 9.0 s → 9.8 s.

One node costs the same as a whole file (3.8 s) because the floor is the
`pyrofork` import, not the tests: there is nothing to be gained by selecting
narrower.

Iterate with `gates` and `file`. Run `quick` or `test` **once per commit**, not
after every edit — re-running the full tier per 3-line change is the single
biggest time sink in this repo. `make gates/quick/t FILE=...` are equivalent
for hosts that have `make`.

Keep new tests fast. Two tests once accounted for 18 s of a 25 s run: one paid
a real `asyncio.sleep` per page it generated, the other waited out the real
production timeout to prove a mechanism. Stub the delay, or shrink the constant
locally and pin the production value in a separate test.

## Test tiers

- **Unit** (every PR): `uv run pytest -m unit --cov=Thunder --cov-report=term-missing --cov-fail-under=35`
  Only `Thunder/__main__.py` is omitted from the report (boot/teardown needs a
  real connection). `bot/plugins/*` is measured: the directory carries an
  `__init__.py` so coverage sees it, and the old omit entry was dead config
  that hid ~900 statements of bot surface from the gate.
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
- **Never `try/except FloodWait`**: use `tg_call(...)` / `reply_safe` / `send_safe` / `edit_safe` / `delete_safe` / `answer_safe`. Allowed inline: `custom_dl.py` streaming/resume, `rate_limiter.py` worker-requeue, `broadcast.py` classify-only catch, `safe_call.py` itself, the server 503 ladder, `rate_limiter.py` notification catch-and-log.
- Budgets: Mongo `timeoutMS=5000`, TG RPC `TG_RPC_TIMEOUT_SECONDS` (transfers unbounded), shortener/keepalive 10 s
- A new media call site through `tg_call` must be added to `UNBOUNDED_SHAPES` (today `Message.copy` and `Message.reply_document`, used by the ingest forward, `/log` and `/shell`) or pass `timeout=0`; the shape set is pinned by `test_safe_call_budgets.py`
- `html.escape()` all user strings in HTML (M7); fail-closed deny with `MSG_ERROR_TEMP` (H7)
- Owner handlers: `filters.user(Var.OWNER_ID)`; PascalCase / snake_case / UPPER_SNAKE_CASE

## Access gates (M12 order: banned → private-mode → token; force-sub where applicable; shortener-status is routing, not a gate)

- Owner bypasses everything, including force-sub; authorized users bypass private-mode + token, but not ban or force-sub.
- `/start` runs only `banned + private-mode` (activation stays reachable).
- `PRIVATE_MODE=True` restricts the whole bot to owner + authorized users.
- No-sender messages (channel posts, anonymous admins) are DENIED wherever a gate is active — fail-closed, never bypass.
- Force-sub admits `OWNER`/`ADMINISTRATOR`/`MEMBER`, or `RESTRICTED` with `is_member`: `get_chat_member` only raises for a user absent from the channel, so banned and left members come back as a `ChatMember` and must be rejected by status.
- New gate = one `PREFLIGHT_GATES` entry + a row above.

## Rate Limiting

- Two-tier deque; **strict alternation** while both tiers are populated, so a
  continuous owner/authorized stream cannot starve regular users (and vice
  versa). A deferred hand-over costs one rotation, never a lost item.
- Queue/wait-estimate UX is protected.
- Charge-at-exec (H6b); FloodWait requeues (max 5); ingest contention requeues
  (max 20, counted separately so one cannot mask the other); RPS breaker burst
  2×, dry requeues (H6c); hard-bounded (`MAX_TRACKED_USERS`) + swept 5 min (H6a).
- The per-user table is bounded at **admission**, not only by the sweep: a timer
  bounds the steady state, not what can be created between ticks. Eviction is by
  **least-recently-active**, never dict order — keys are only inserted when
  absent, so dict order is first-ever-seen, and evicting by it hands a currently
  active user a fresh rate-limit window. Admission and sweep share one
  `_evict_inactive_users` so they cannot disagree.
- The RPS breaker reports **nothing** unless `GLOBAL_RPS_LIMIT > 0` or
  `GLOBAL_RATE_LIMIT` is on, and both ship disabled. Any code that consults it
  as its only pressure signal is dead on a default deployment; `stream.py`'s
  batch pacing reads queue depth as well for exactly that reason.

## Stream slots and the transfer deadline

`work_loads[client_id]` is the admission counter for `/f/` and `/watch/`. One
`_slot_release` closure per request is shared by the admission ladder, the body
generator's `finally`, and the stall watchdog, so whichever gets there first
releases exactly once and the counter cannot go negative (negative means
`select_optimal_client` believes there is spare capacity, which is worse than
the leak).

A stalled reader leaves the body generator suspended at a `yield`, so its
`finally` never runs and the slot leaks permanently. A watchdog task reclaims it
at a **wall-clock** deadline — `size / 64 KiB/s`, floored at 120 s and capped at
1800 s. It is a bound on the leak, not a stall detector: there is no idle check,
so a client sustaining less than `size / 1800 s` is cut mid-transfer with
`Content-Length` already sent and must resume with a `Range`. Prompt stall
detection needs progress-based reaping, which is not implemented.

## Shutdown budget

The teardown steps share one `_SHUTDOWN_BUDGET_SECONDS` budget (10 s), with a
per-step floor so a spent budget cannot turn `db.close()` into a 0 s timeout and
record a failure that never happened. `cleanup_clients` is the exception: it
stops each client individually at 10 s, so it gets **its own slice** (`shared=False`
in `_teardown_plan`) rather than drawing on the shared pool — with a shared
budget the grant is `min(cap, budget_left)` and scaling the cap achieves nothing.
Worst case is `budget + shared_steps × floor + _CLIENT_STOP_MAX_SECONDS` = 28 s,
inside the 30 s that Heroku, Render and Kubernetes allow; the import-time assert
and `test_shutdown_budget.py` both hold it there.


## URL families

- Canonical `/f/<32-hex>/<name>`, `/watch/f/<32-hex>/<name>` (L4); legacy `/watch/<6-char-hash><id>/<name>` valid, `ENABLE_LEGACY_LINKS` off → 410 (default on). 20- and 32-hex validate side-by-side forever; legacy pages cached.

## Configuration

Copy `config_sample.env` → `config.env` (+ optional `config.env.local` overrides).
Required: `API_ID`, `API_HASH`, `BOT_TOKEN`, `BIN_CHANNEL`, `OWNER_ID` (boot refuses), `DATABASE_URL`.
New env vars ship a safe default + annotated sample entry in the same PR.

## Debugging

- Logs `Thunder/logs/bot.txt` (10 MiB × 5; `/log` redacted); `GET /health` (dep-free); `GET /status` (no-store: DC, inflight, touch stats); admin `/stats` (limiter).
- CI bar: ruff, mypy, pytest, pip-audit, bandit, vulture, dependency-count — all green.
