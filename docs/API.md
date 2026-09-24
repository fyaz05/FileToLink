# HTTP API

Base URL: `Var.URL` (built from `FQDN`/`HAS_SSL`/`PORT`/`NO_PORT`). All file
URLs are capability links — anyone with the URL can download.

## Routes

| Method | Path | Description |
|---|---|---|
| `GET`, `HEAD` | `/health` | Liveness, dependency-free (`{"status":"ok"}`, `no-store`) |
| `GET`, `HEAD` | `/` | Redirect to the project repo |
| `GET`, `HEAD` | `/status` | Telemetry (`Cache-Control: no-store`): version, uptime, bot username, client count, per-client inflight + DC |
| `GET` | `/activate/{token}` | Redirects to `https://t.me/<bot>?start=<token>`; never consumes (burn happens in `/start`) |
| `GET`, `HEAD` | `/watch/f/{hash}/{name}` | Player page (20- or 32-hex hash, side-by-side forever) |
| `GET`, `HEAD` | `/watch/{path}` | Player page (legacy links, name optional; `ENABLE_LEGACY_LINKS=False` → `410`) |
| `GET`, `HEAD` | `/f/{hash}/{name}` | Byte stream (canonical); `HEAD` holds an admission ticket so probes reflect load |
| `GET`, `HEAD` | `/{path}` | Byte stream (legacy links; same `410` gate) |
| `OPTIONS` | `/{path}` | CORS preflight (`200`; path must be non-empty) |

## Link families

- Canonical: `/f/<32-hex>/<name>` (+ `/watch/f/...` player). 20-hex hashes from
  older uploads validate side-by-side forever.
- Legacy: `/watch/<hash><id>/<name>` (player) and `/<hash><id>/<name>`
  (bytes). Disable via `ENABLE_LEGACY_LINKS=False`.
- Filenames are slash-normalized (`/` → `_`) then percent-encoded;
- `?disposition=inline` streams in-browser, anything else (or absent) downloads
  as `attachment` (`filename` + RFC 5987 `filename*`).

## Ranges (RFC 7233)

`Range: bytes=<start>-<end>` → `206` + `Content-Range`. Oversized ends clamp;
suffix ranges (`bytes=-N`) serve the tail; full-file `0-(size-1)` normalizes to
`200` without `Content-Range`. Garbage/multi-range → `400` (deliberate, stricter
than RFC 9110's ignore); unsatisfiable → `416` + `Content-Range: bytes */<size>`.

## Errors

| Status | When | Headers |
|---|---|---|
| `400` | malformed range / token (bad hash shape → `404`) | CORS, `no-store` |
| `404` | unknown hash, evicted stale record, zero-size file | `no-store`, CORS |
| `410` | legacy links with `ENABLE_LEGACY_LINKS=False` | `no-store`, CORS |
| `416` | unsatisfiable range | `Content-Range`, CORS, `no-store` |
| `500` | unexpected failure (error-id body, no internals) | CORS, `no-store` |
| `503` | no free client (`Retry-After: 2`) / pool saturated (`Retry-After: 2`) / index, Telegram, or startup brownout (`Retry-After: 5`) | `no-store`, CORS |

Bodies are fixed wordings per site (capacity vs brownout variants; `500`s carry
a support error-id, never internals) — retries key off status + `Retry-After`,
never body text. Transient vault/Telegram failures never delete file records
(only a confirmed absence self-heals to `404`). Mid-stream transport failures
truncate the body (headers already sent) — resume with `Range`.

## Caching / CORS / robots

- Bytes: `Cache-Control: public, max-age=31536000` + `Accept-Ranges: bytes`.
  Every error is `no-store` so caches never pin a failure.
- `Access-Control-Allow-Origin: *` (allowed: `Range, Content-Type, *`;
  `Content-Length, Content-Range, Content-Disposition` exposed).
  No credentials — the link itself is the secret.
- Player pages: `<meta name="robots" content="noindex, nofollow">` +
  `X-Robots-Tag: noindex, nofollow`.
