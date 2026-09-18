# Tuning

Start with defaults. Touch knobs only with a measured reason (see
`scripts/bench.py` for the local harness).

## Capacity rule

Peak Telegram RPC ≈ `active streams × per-stream concurrency`. Size
`MAX_CONCURRENT_STREAMS` so `clients × cap` fits the host's file descriptors
and upstream patience — not the other way round.

| Host | Workers / batch / broadcast | Streams/client | Notes |
|---|---|---|---|
| Small VPS (1 vCPU, 1GB) | `5 / 5 / 4` (defaults) | `8` (default) | Defaults are the small preset |
| Medium VPS (2 vCPU, 4GB) | `8 / 8 / 6` | `12` | Raise only after watching `FLOOD_WAIT` |
| Big box (4+ vCPU, 8GB+) | `12 / 10 / 8` | `16` | Watch Mongo `timeoutMS=5000` pressure first |

Scale gradually: floods mean back off, not up. A `FloodWait` storm after a
raise is the signal to revert, not to retry harder.

## Flood guidance

- Sleeps belong in `tg_call` (bounded, `min(value, 30)`); never sleep a pool
  worker inline. Sustained floods should surface as 503 + `Retry-After`, not
  silent stalls — check `Retry-After` headers before raising limits.
- Keep pyrofork auto-sleep ceiling (`SLEEP_THRESHOLD`) below
  `TG_RPC_TIMEOUT_SECONDS` so waits surface to `tg_call` instead of hiding
  inside the library.

## Rate limiting

Enable only under abuse: `RATE_LIMIT_ENABLED` + `MAX_FILES_PER_PERIOD` /
`RATE_LIMIT_PERIOD_MINUTES`. Prefer the global breaker (`GLOBAL_RPS_LIMIT`,
burst = 2× rate) for provider-level protection — it shapes instead of
dropping. Queue UX (wait estimates) is protected behavior; don't tune it away.

## Appendix: validation errors (boot refuses until all are fixed)

| Symptom (log line) | Cause | Fix |
|---|---|---|
| `API_ID=0 … must be >= 1` / `is required` | placeholder never set | Set real `API_ID` from my.telegram.org |
| `OWNER_ID … min 1` / boot refusal | no owner configured | Set `OWNER_ID` (owner checks would match nobody) |
| `BIN_CHANNEL` / `DATABASE_URL … is required` | missing values | Fill both; links and users need them |
| `… must be >= / <= …` | knob outside its bound | Read the bound in `config_sample.env`, adjust |
| `… is not a valid integer/number` | junk in a numeric knob | Use plain digits (no quotes needed, no units) |
| `PRIVATE_MODE and TOKEN_ENABLED cannot both be enabled` | gates unreachable together | Pick one mode |
| `Shortener enabled but … missing` (warning) | `SHORTEN_*` on without site + key | Set both or turn the feature off |
| `FQDN is not set` (warning) | links fall back to bind address | Set public domain/IP for reachable links |
| `BANNED_CHANNELS=… has non-integer entries` | junk token in the set | Space-separated `-100…` IDs only |
