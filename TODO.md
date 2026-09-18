# TODO — personal (move out of repo)

Branch: `feat/improvement-plan` @ `90e6451`. PR: #50 (open).

## Done (shipped)
- [x] Template/docs commit + ThunderGo player UX port (theme, drawer keys, size meta)
- [x] Phase 1: owner-bypasses-everything (+ test)
- [x] Phase 2: 8 ThunderGo message takes (+ dead-const cleanup)
- [x] Phase 3: disposition split, error-header uniformity, broadcast retry/prune, docs/API.md
- [x] Phase 4: listauth pagination, image attestations
- [x] Phase 5: player contract tests (203 unit green)
- [x] Lean passes: AGENTS.md 185→102, comment trims
- [x] Safe batch: og embeds, TUNING.md, deploy manifests, property/leak tests, bench harness
- [x] Token tz-hardening: naive→UTC, junk→invalid (pre-deploy Mongo query obsolete)

## Ship
- [ ] CI green on PR #50 (pip-audit, bandit, vulture, dep-count, Docker smoke, integration tier)
- [ ] Merge; watch first boot (index ensures, backfill cursor, gate-mode line) and first brownout (503 + Retry-After, no self-heal deletes)

## Leanness part 2 (one PR, behavior-neutral)
- [ ] New `Thunder/utils/ttl_cache.py`; migrate 4 caches (flag_cache, canonical_files, render_template legacy cache, shortener) — ~70L saved
- [ ] Shared t.me deep-link builder (CORS/503/no-store consts already done via `_ERROR_HEADERS`)
- [ ] Split `rate_limiter.py` (breaker / queues / workers / notify + facade); cut per-file ETAs + occupancy vanity — ~80L
- [ ] Collapse shortener to Bitly + Generic only (drop Ouo/CuttLy) — ~90L
- [ ] `VAULT_FETCH_TIMEOUT_SECONDS` const (replaces 4× literal `timeout=60`); `_admission_ladder` try/finally for the 4× decrement
- [ ] CI hygiene: `astral-sh/setup-uv`, path filters for docs-only PRs, CI-calls-`make` dedupe, `collMod` for TTL changes, vulture-whitelist procedure, document why `RUF` opted out / drop `B101` skip

## Robustness (schedule vs real usage)
- [ ] Pyrofork exit plan (upstream archived 2026-09): evaluate maintained fork or pyrogram, session compat, full soak before switching
- [ ] Honor `If-Range` / `If-Unmodified-Since` on media responses (stale-resume gap)
- [ ] Add `"force_sub"` to `PREFLIGHT_GATES` (remove must-remember-to-call trap)
- [ ] Tests: `build_file_record`, `_infer_mime_type`, wait-estimate text, priority-drain-first, `QueueFull`, singleflight/LRU eviction, `_serve_media_response` HEAD + zero-size paths
- [ ] Observability: counters around ladder/breaker decisions; alert on 503-rate spikes

## From-others backlog (ThunderGo / FSB / mtgo / gotd research)
- [x] Property-based parser tests (range/hash validators)
- [x] Leak regression tests (task/GC accounting under cancel storms)
- [x] Throughput benchmark harness (`scripts/bench.py`, hermetic)
- [x] `docs/TUNING.md` (VPS presets, capacity rule, flood guidance, validation table)
- [x] `docker-compose.yml` for local dev; `render.yaml`/`app.json` one-click deploys
- [x] `og:video`/`og:audio` embed tags
- [ ] Concurrent range prefetch (`min(concurrency, remaining)` workers, ordered emit) — biggest perf win, medium risk
- [ ] Stall watchdog on byte flow (zero bytes for N sec → abort, distinct from flood cap)
- [ ] Bounded-retry audit (caps + backoff + jitter on every loop in `custom_dl`/ingest)
- [ ] Retry telemetry (FloodWait hits, ref-refreshes, chunk retries → `/status`)

## Deliberately skipped (don't reopen without reason)
- Distroless/read-only image (app writes logs+sessions), GCRA-for-deque swap,
  LINK_CHUNK_SIZE change, chooser/VLC-beta drawer entries, bare-503 admission,
  owner-bypass of nothing (owner skips all)
- Adaptive chunk sizing (decision: fixed 1MiB is better — uniform wire windows,
  stable CDN/part boundaries, simpler resume math)
