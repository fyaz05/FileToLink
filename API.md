# API Documentation — Thunder FileToLink

## HTTP Endpoints

### `GET /`
Redirects to the GitHub repository.

### `GET /health`
Health check endpoint for load balancers.

**Response:** `200 OK`
```json
{"status": "ok"}
```

### `GET /metrics`
Prometheus-compatible metrics endpoint (requires `ADMIN_TOKEN` header if configured).

**Response:** `200 OK` (text/plain)
```
# HELP thunder_uptime_seconds Time since bot started
thunder_uptime_seconds 8130
# HELP thunder_active_streams Currently active file streams
thunder_active_streams 3
# HELP thunder_bytes_served_total Total bytes served via streaming
thunder_bytes_served_total 1073741824
# HELP thunder_requests_total Total HTTP requests by path
thunder_requests_total{path="/f/hash123/file.mp4"} 42
# HELP thunder_errors_total Total HTTP errors by status code
thunder_errors_total{status="404"} 5
```

### `GET /status`
Returns bot status and resource usage (requires `ADMIN_TOKEN` header if configured).

**Response:** `200 OK`
```json
{
  "status": "operational",
  "version": "2.1.0",
  "uptime": "2h 15m 30s",
  "active_clients": 1,
  "total_workload": 3
}
```

### `GET /watch/f/{public_hash}/{filename}`
Renders an HTML streaming page with video player and download button.

**Parameters:**
- `secure_hash` — 6-character file unique ID prefix
- `message_id` — Telegram message ID
- `filename` — Original filename (for display)

**Response:** `200 OK` (text/html) — Cinema streaming player

### `GET /watch/f/{public_hash}/{filename}`
Renders an HTML streaming page for canonical (deduplicated) files.

**Parameters:**
- `public_hash` — 20-character SHA256 hash of file unique ID
- `filename` — Original filename

**Response:** `200 OK` (text/html) — Cinema streaming player

### `GET /f/{secure_hash}{message_id}/{filename}`
Downloads or streams a file directly.

**Parameters:**
- `secure_hash` — 6-character file unique ID prefix
- `message_id` — Telegram message ID
- `filename` — Original filename

**Query Parameters:**
- `disposition` — `inline` or `attachment` (default: `attachment`)

**Headers:**
- `Range` — Byte range for partial content (e.g., `bytes=0-1023`)

**Response:**
- `200 OK` — Full file
- `206 Partial Content` — Range request
- `404 Not Found` — File not found

### `GET /f/{public_hash}/{filename}`
Downloads or streams a canonical (deduplicated) file.

**Parameters:**
- `public_hash` — 20-character SHA256 hash
- `filename` — Original filename

**Response:** Same as above.

### `HEAD` on any endpoint
Returns headers without body (same as GET but no content).

### `OPTIONS` on any endpoint
Returns CORS preflight headers.

---

## Error Responses

| Status | Meaning |
|--------|---------|
| `200` | Success |
| `206` | Partial content (range request) |
| `302` | Redirect |
| `400` | Bad request (invalid parameters) |
| `404` | Resource not found |
| `416` | Range not satisfiable |
| `500` | Internal server error (with error ID) |
| `503` | All clients at capacity |

---

## Telegram Bot Commands

### User Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and token activation |
| `/help` | Usage instructions |
| `/about` | Bot information |
| `/link [n]` | Generate links (reply to file in groups, `n` for batch) |
| `/dc` | Data center info for user or file |
| `/ping` | Check bot responsiveness |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/status` | Bot status and workload |
| `/stats` | System resource usage |
| `/users` | Total user count |
| `/broadcast [mode]` | Send message to all users |
| `/ban <id> [reason]` | Ban a user or channel |
| `/unban <id>` | Unban a user or channel |
| `/authorize <id>` | Grant permanent access |
| `/deauthorize <id>` | Revoke permanent access |
| `/listauth` | List authorized users |
| `/log` | Send bot log file |
| `/restart` | Restart the bot |
| `/shell <cmd>` | Execute shell command (60s timeout) |
| `/speedtest` | Network speed test |
