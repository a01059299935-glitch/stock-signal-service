# Stock Site Improvements and Free Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix chart date semantics, add Korean search for KRX and international stocks, preserve missed news/filings in a separate digest, and prepare the single-user app for a free sleeping Render deployment with Neon storage and R2 backups.

**Architecture:** Keep the React UI, static assets, FastAPI APIs, and browser WebSocket on one Render web service/origin. Use Neon PostgreSQL for durable primary records and encrypted compressed snapshots in Cloudflare R2; catch-up refresh runs before a wake digest is called current. OpenDART supplies Korean company identifiers/filings; Finnhub's real-time trade path stays intact for supported US tickers and is never used to mark a KRX ticker LIVE.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL/Neon, Cloudflare R2 S3 API, Finnhub REST/WebSocket, OpenDART, React 19, Lightweight Charts 5, Render Blueprint.

**Spec:** `docs/superpowers/specs/2026-09-26-sleeping-site-deployment-design.md`; subsystem contracts are `2026-09-26-chart-time-design.md`, `2026-09-26-korean-search-design.md`, and `2026-09-26-catchup-storage-design.md` in the same directory.

## Global Constraints

- Keep the existing Finnhub US trade WebSocket -> FastAPI -> browser real-time price path intact.
- No mock prices, REST price polling, or fabricated market events.
- Preserve the existing detailed news and SEC list and add a separate digest.
- No automated orders; signals remain informational.
- Keep the current single-user login protection at the public edge.
- Free hosting may sleep and cold-start; there is no background collection while the web service is stopped.
- Store durable records outside the ephemeral web container.
- Never silently delete saved news/filings or backups to meet quotas.
- KRX data must never be marked LIVE from the Finnhub US trade stream.

## Review Focus

- KRX quote lacks an actual provider trade: render delayed/unavailable status, never LIVE.
- Catch-up provider fails after long sleep: retain rows and watermark; show source coverage and stale timestamp.
- A provider omits an old article: keep the previously stored record and deduplicate by fingerprint.
- A daily candle is encoded as an instant: show the exchange session date, not a 22:30/23:30 market-open label.
- Neon/R2 quota or credentials fail: preserve current records and stop archive writes before the configured cap; never trigger paid usage.

---

### Task 1: Fix chart time and date granularity

**Files:**
- Modify: `backend/app/providers.py` (candle normalization/aggregation output)
- Modify: `frontend/src/main.tsx` (KST formatters, business-day input, timezone label)
- Modify: `frontend/src/style.css` (chart timezone label only)
- Test: `backend/tests/test_chart_times.py`

**Interfaces:**
- Backend `to_chart_time(timestamp_seconds: int, timeframe: str) -> int | str` returns epoch seconds for intraday frames and exchange-local `YYYY-MM-DD` for day/week/month frames.
- Frontend converts actual live event timestamps to the matching session date only for date-granularity series; `liveTrades` payloads remain unchanged.

- [ ] Add a failing test:

```python
def test_daily_session_timestamp_is_date_not_market_open_time():
    assert to_chart_time(1790343000, "1D") == "2026-09-25"
```

- [ ] Run `cd backend; python -m pytest tests/test_chart_times.py -q` and confirm current daily chart time fails the date assertion.
- [ ] Add tests for an intraday Unix timestamp rendered as 22:30 KST and a US session crossing a DST boundary.
- [ ] Implement backend date-time serialization while keeping exchange-session aggregation in America/New_York.
- [ ] Update Lightweight Charts formatters to show KST for instants and trading dates for daily/weekly/monthly values; add a visible `KST` label.
- [ ] Preserve the current chart instance and series; ensure a real trade updates only the current candle.
- [ ] Run focused tests and `cd frontend; npm run build`; inspect RT, 1D, 3M, 5Y, and ALL candle responses.

### Task 2: Add Korean-name search and KRX data handling

**Files:**
- Modify: `backend/app/config.py` and `.env.example` (OpenDART server key)
- Modify: `backend/app/providers.py` (OpenDART directory/cache, merged search, KRX filings and source freshness)
- Modify: `backend/app/tasks.py` (persist SEC and OpenDART filings)
- Modify: `frontend/src/main.tsx` (search choices, exchange and freshness labels)
- Test: `backend/tests/test_korean_search.py`

**Interfaces:**
- `_load_korean_directory() -> list[dict]` returns cached `{ticker, name, exchange, market}` rows from the server-side OpenDART company directory.
- `search_stocks(query, limit)` merges Hangul alias, English-name, and ticker matches; KRX codes canonicalize to `.KS`/`.KQ` and retain the six-digit display code.
- KRX market status is `EOD`, `DELAYED`, or `UNAVAILABLE` unless an actual KRX trade feed is configured.

- [ ] Add a failing test with a mocked directory:

```python
async def test_hangul_name_search_returns_krx_code(monkeypatch):
    monkeypatch.setattr(providers, "_load_korean_directory", fake_korean_directory)
    rows = await providers.search_stocks("삼성전자", limit=5)
    assert rows[0]["ticker"] == "005930.KS"
    assert rows[0]["market"] == "KRX"
```

- [ ] Run the focused test and confirm the current English-only provider does not match Hangul.
- [ ] Add tests for ticker-only (`005930`), `.KS`/`.KQ`, English, missing key, and an ambiguous alias.
- [ ] Load/caches OpenDART names using `OPENDART_API_KEY` only from backend settings; merge them with Finnhub/SEC international results.
- [ ] Add OpenDART filing fetch and retain existing SEC behavior; identify each filing source correctly.
- [ ] For KRX watchlist items, use only a verified external quote/candle source and show its source/as-of time, or show unavailable; never use Finnhub US trades or synthetic movement.
- [ ] Run focused tests and the frontend build. If the DART key is absent, surface the setup requirement and do not claim full KRX-name coverage.

### Task 3: Add persistent missed-news digest and retention watermark

**Files:**
- Modify: `backend/app/models.py`; create one-user app-state Alembic migration
- Modify: `backend/app/tasks.py` (non-destructive source persistence)
- Modify: `backend/app/main.py` (single-flight catch-up/digest endpoint)
- Modify: `frontend/src/main.tsx` and `frontend/src/style.css` (sync state and digest panel)
- Test: `backend/tests/test_catchup_digest.py`

**Interfaces:**
- `POST /api/digest/refresh` returns `{since, synced_at, items, stale}`; it advances `last_successful_visit_at` only after all watchlist refreshes and digest construction succeed.
- `build_missed_digest(records, last_visit_at)` returns only records with `published_at > last_visit_at`, grouped by ticker/impact, with provider titles, summaries, and source links.

- [ ] Add tests for first visit, one new item after the watermark, duplicate fingerprint, partial provider failure, and unchanged watermark on failure.
- [ ] Run the focused tests and confirm there is currently no persisted last-visit watermark or digest endpoint.
- [ ] Add the app-state model/migration and a single-flight catch-up guard so concurrent wake requests do not refresh twice.
- [ ] Stop deleting saved records just because a provider omitted them; preserve old rows and deduplicate by source fingerprint.
- [ ] Add `지난 소식 확인 중`, digest success, stale-data, no-new-items, and provider-lookback states to the frontend; keep detailed `최신 뉴스 · 공시` panel unchanged.
- [ ] Do not advance the watermark after failed refresh; run focused tests and `npm run build`.

### Task 4: Add Neon primary storage and R2 archive/restore

**Files:**
- Modify: `backend/app/db.py` (pooled Neon URL, TLS, reconnect)
- Modify: `backend/app/config.py` and `.env.example` (Neon/R2 credentials, backup key and size cap)
- Create: `backend/app/backup.py` (compressed encrypted dump, upload, restore manifest)
- Modify: `backend/app/main.py` (best-effort snapshot after successful catch-up)
- Modify: `backend/requirements.txt` and root Dockerfile (S3 client, encryption, PostgreSQL dump utility)
- Modify: `README.md` (provider setup, quotas, restore steps)
- Test: `backend/tests/test_backup.py`

**Interfaces:**
- `create_backup_if_due(now)` writes an encrypted, compressed, date-stamped DB dump to a private R2 bucket after a successful catch-up when the newest archive is at least one day old.
- Stop new archive writes before `R2_MAX_STORAGE_BYTES` (default 9 GB); preserve old archives and primary DB rows. Never switch to paid usage automatically.

- [ ] Add tests for encryption/decryption, dated archive names, not-due behavior, configured size-cap refusal, missing credentials, and upload failure using a fake S3 client.
- [ ] Run focused tests and verify all log messages omit credential values.
- [ ] Implement encryption, compression, private-bucket upload, checksum manifest, and a documented restore command; restore only after migrations match the dump schema.
- [ ] Configure Neon connections with TLS, `pool_pre_ping`, bounded pool/idle lifetime, and handling for compute wake/reconnect.
- [ ] Expose Neon DB size and R2 archive usage in health/status; warn at 80%, stop uploads at the configured cap, and preserve all rows/archives.
- [ ] Run focused tests and `npm run build`; confirm backup upload failure does not roll back saved news.

### Task 5: Package a single authenticated Render Free web service

**Files:**
- Create: root `Dockerfile` (build React, serve static app and FastAPI on Render `PORT`)
- Create: root `.dockerignore` (exclude `.env`, local databases, secrets, `node_modules`, generated artifacts)
- Create: `render.yaml` (one Free web service, health check, secret prompts)
- Create: `backend/app/auth.py`; modify `backend/app/main.py` (same-origin SPA/API and HTTP/WS Basic Auth)
- Modify: `.env.example` and `README.md` (public deployment steps without secrets)
- Test: `backend/tests/test_public_auth.py`

**Interfaces:**
- One service serves React, HTTP API, and `/api/ws/trades` on one origin, behind the existing single-user Basic Auth behavior.
- `render.yaml` uses a free `onrender.com` subdomain and `/api/health`; runtime `DATABASE_URL` points to Neon. Every secret is entered in the provider dashboard only.

- [ ] Add tests for anonymous HTTP/API rejection, valid Basic Auth, WebSocket auth, and SPA asset fallback.
- [ ] Run tests/build and verify `.env`, DB URL, Finnhub, Telegram, OpenDART, R2, and backup-key values are absent from image/build context.
- [ ] Add the single-service Dockerfile and Render Blueprint. Bind FastAPI to `0.0.0.0:$PORT` and keep local Docker Compose behavior available.
- [ ] Document free sleep/cold start, provider lookback, KRX market-data freshness, Neon/R2 setup, and restoration.
- [ ] Do not create external resources, enable paid plans, or expose the actual public URL until provider accounts are connected and the user reviews URL/auth behavior.

## Handoff and release gates

- This project folder currently has no `.git` directory. Use a private source repository for Render or another user-selected supported artifact path; do not publish source code publicly.
- User must configure Render, Neon, Cloudflare R2, and OpenDART credentials in their provider dashboards; never request secret values in chat.
- Before exposing a public URL, present the URL and authentication behavior and obtain action-time confirmation.
- Verify the deployed URL, durable database, catch-up digest, chart labels, KRX freshness, and actual Finnhub trade path after connected accounts are available. Do not call local build success a public deployment.

