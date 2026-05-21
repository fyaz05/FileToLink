# Architecture — Thunder FileToLink

## System Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Telegram API (TDLib)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                │
│  │ Primary  │ │ Client 1 │ │ Client N │  Multi-client   │
│  │  Client  │ │          │ │          │  load balancing  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘                │
│       └────────────┼────────────┘                       │
└────────────────────┼────────────────────────────────────┘
                     │
┌────────────────────┼────────────────────────────────────┐
│                    ▼                                     │
│  ┌─────────────────────────────────────────┐            │
│  │              Bot Plugins                 │            │
│  │  ┌─────────┐ ┌──────────┐ ┌──────────┐ │            │
│  │  │ admin   │ │ common   │ │ stream   │ │            │
│  │  │ /status │ │ /start   │ │ /link    │ │            │
│  │  │ /ban    │ │ /help    │ │ media    │ │            │
│  │  └─────────┘ └──────────┘ └────┬─────┘ │            │
│  └─────────────────────────────────┼───────┘            │
│                                    │                     │
│  ┌─────────────────────────────────▼───────┐            │
│  │           Rate Limiter                   │            │
│  │  Priority queue (authorized users)       │            │
│  │  Regular queue (all users)               │            │
│  │  Owner bypass                            │            │
│  └─────────────────────────────────┬───────┘            │
│                                    │                     │
│  ┌─────────────────────────────────▼───────┐            │
│  │         Canonical Files Service          │            │
│  │  ┌───────────┐  ┌──────────────┐        │            │
│  │  │ LRU Cache │  │ Ingest Lock  │        │            │
│  │  │ (3 dicts) │  │ (MongoDB)    │        │            │
│  │  └───────────┘  └──────────────┘        │            │
│  │  File deduplication via SHA256 hash      │            │
│  └─────────────────────────────────┬───────┘            │
│                                    │                     │
│  ┌─────────────────────────────────▼───────┐            │
│  │           MongoDB (Database)             │            │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ │            │
│  │  │ UserRepo │ │ BanRepo  │ │ FileRepo │ │            │
│  │  └──────────┘ └──────────┘ └──────────┘ │            │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ │            │
│  │  │TokenRepo │ │ LockRepo │ │RestartRepo│ │            │
│  │  └──────────┘ └──────────┘ └──────────┘ │            │
│  └─────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────┘
                     │
┌────────────────────┼────────────────────────────────────┐
│                    ▼                                     │
│  ┌─────────────────────────────────────────┐            │
│  │           HTTP Server (aiohttp)          │            │
│  │  Middleware: CORS, Error, Metrics        │            │
│  │  ┌─────────┐ ┌──────────┐ ┌──────────┐ │            │
│  │  │ /watch/ │ │ /f/      │ │ /health  │ │            │
│  │  │ /status │ │ /metrics │ │          │ │            │
│  │  └─────────┘ └──────────┘ └──────────┘ │            │
│  └─────────────────────────────────────────┘            │
│                                                         │
│  ┌─────────────────────────────────────────┐            │
│  │         ByteStreamer                     │            │
│  │  downloadFile → aiofiles → chunked HTTP │            │
│  └─────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────┘
```

## Data Flow

### File Upload → Link Generation

1. User sends file to bot (private chat or group `/link`)
2. `stream.py` validates: ban check → token check → force channel check
3. Rate limiter checks limits, queues if needed
4. `canonical_files.py` checks if file already exists (by `file_unique_id`)
5. If new: forward to `BIN_CHANNEL`, build `FileRecord`, store in MongoDB
6. If exists: reuse canonical record, increment `reuse_count`
7. Generate links: `/{hash}{msg_id}/{name}` and `/watch/{hash}{msg_id}/{name}`
8. Send links to user (DM + group reply)

### HTTP Request → File Streaming

1. Client requests `/f/{hash}{msg_id}/{name}`
2. `stream_routes.py` parses hash + message_id from URL
3. `select_optimal_client()` picks client with lowest workload
4. `work_loads[client_id] += 1`
5. `ByteStreamer.get_file_info()` fetches message metadata
6. Hash validation: `file_unique_id[:6] == secure_hash`
7. `stream_file()` downloads file via TDLib, then streams via `aiofiles`
8. Range requests supported via `parse_range_header()`
9. `work_loads[client_id] -= 1` in `finally` block

### Canonical File Deduplication

Same file uploaded by different users → single BIN copy:

1. `build_public_hash(file_unique_id)` → 20-char SHA256 prefix
2. Check LRU cache → check MongoDB → if found, reuse
3. If not found: acquire ingest claim (MongoDB lock)
4. Forward to BIN_CHANNEL, build `FileRecord`
5. Store in MongoDB + LRU cache
6. Release ingest claim

## Key Components

### Rate Limiter (`rate_limiter.py`)
- Two deque queues: priority (authorized) and regular
- Owner bypasses all limits
- Exponential backoff on requeue (0.5s → 1s → 2s → 4s → 8s)
- Max 5 requeues before dropping
- Periodic cleanup of stale entries (5-min interval)

### Multi-Client (`clients.py`)
- Primary client + N additional clients from `MULTI_TOKEN*` env vars
- Each client has own TDLib instance and data directory
- `work_loads` dict tracks concurrent streams per client
- Backpressure: reject when all clients at `MAX_CONCURRENT_PER_CLIENT` (8)

### Caching (`canonical_files.py`)
- Three LRU OrderedDict caches: by unique_id, by hash, by message_id
- 600s TTL, 4096 max items per cache
- Periodic pruning every 50 inserts
- Background touch batching (10s delay)

### Database (`database/`)
- Mixin-based repository pattern
- MongoDB connection pool: maxPoolSize=50, minPoolSize=5
- User existence cache: 5-min TTL
- Ban status cache: 5-min TTL with invalidation on ban/unban

## Configuration

All config via environment variables (see `config_sample.env`).

**Required:** `API_ID`, `API_HASH`, `BOT_TOKEN`, `BIN_CHANNEL`, `DATABASE_URL`

**Key Optional:**
- `MULTI_TOKEN1..49` — Additional bot tokens
- `FORCE_CHANNEL_ID` — Required channel join
- `TOKEN_ENABLED` — Token-based access control
- `RATE_LIMIT_ENABLED` — Rate limiting
- `GLOBAL_RATE_LIMIT` — Global request throttling

## Deployment

- **Docker:** `docker build -t thunder . && docker run thunder`
- **uv:** `uv sync && uv run python -m Thunder`
- **pip:** `pip install -r requirements.txt && python -m Thunder`

Health check: `GET /health`
Metrics: `GET /metrics`
