# Sleeping Stock Site: Charts, KRX Search, Catch-up Digest, and Free Hosting

Date: 2026-09-26
Status: User-approved direction; implementation plan follows

## Goal

Publish the single-user stock-monitoring app at a free HTTPS subdomain. The app may sleep while unused. On return, retain previous records, fetch recent news and filings, and show a separate digest of new items since the last successful visit. Support Korean-name search for both KRX and supported international equities.

## Constraints

- Keep the existing Finnhub US trade WebSocket -> FastAPI -> browser real-time price path intact.
- No mock prices, REST price polling, or fabricated market events.
- Preserve the existing detailed news and SEC list and add a separate digest.
- No automated orders; signals remain informational.
- Keep the current single-user login protection at the public edge.
- Free hosting may sleep and cold-start. No provider refresh loop or live-trade stream runs while the web service is stopped.
- Store durable records outside the ephemeral web container. Never silently delete saved news/filings to meet a quota.

## Proposed experience

1. Chart intraday timestamps in Asia/Seoul (KST), labeled as KST. Daily, weekly, and monthly candles use trading-date values, not market-open Unix times such as 22:30. The chart instance and live trade path stay intact.
2. Keep chart period controls RT, 1D, 1W, 3M, 1Y, 5Y, ALL.
3. Keep the existing `최신 뉴스 · 공시` list. Add `놓친 소식 요약` as a separate panel that groups new records by ticker and impact, links originals, and covers the time since the last successful visit. Use source headlines/provider summaries and SEC/OpenDART filing metadata; do not call it AI analysis.
4. On return after sleep, show `지난 소식 확인 중`, perform one deduplicated catch-up refresh, render the digest after successful sync, then advance the user's last-visit watermark. The first visit shows a 24-hour available-news baseline. If sync fails, retain old data, show a stale-data time, and do not advance the watermark.
5. Search by Korean company name, English name, or ticker for KRX listings and supported US/international listings. Results show symbol and exchange. KRX legal-name coverage uses the official OpenDART listed-company directory; overseas Korean aliases use a curated alias set while English/ticker search continues across the provider's available universe. If an alias is ambiguous, display choices.

## Data and lifecycle

- Persist watchlist, snapshots, news, disclosures, signal history, and last successful digest watermark in Neon PostgreSQL. Neon is separate from the Render web container and can scale database compute to zero while keeping storage separate.
- Current Neon Free limits are 0.5 GB/project, 100 CU-hours/project/month, and 5 GB egress/month. Track size/usage, alert before limits, and never prune records automatically. Provider limits/prices can change.
- Create encrypted, compressed `pg_dump` snapshots in Cloudflare R2 Standard as a second independent archive. Take a snapshot after a successful catch-up if the latest archive is at least one day old; preserve dated snapshots without auto-deletion. Track the current free 10 GB-month storage and stop uploads at the configured safe cap before any overage.
- R2's documented free allowance currently includes 10 GB-month storage, 1 million Class A, 10 million Class B requests per month and free egress. R2 activation and scoped write credentials must be configured in the user's Cloudflare account.
- Protect both providers' credentials in host secret settings. Never print or commit them. Keep a manual export/restore path and document how to restore R2 snapshots into Neon.
- The Render Free Postgres database is not used because it expires after 30 days.
- The backend currently refreshes watchlist data every 300 seconds and runs one refresh immediately when starting. Wake flow must expose sync completion to frontend before marking the digest current.
- Finnhub news currently requests a 7-day lookback and SEC scans 30 days. Show these coverage limits. Increase source lookback only within provider limits. Store provider records without deleting older records just because they are absent from a later response.
- After catch-up succeeds, query records published after the stored watermark and then update it.
- No real-time price trades or transitions during sleep can be reconstructed. After wake, status remains CONNECTING until a real trade is received.

## Korean listings and market-data limits

- Use OpenDART's official corporation-code directory to map Korean company legal names to six-digit stock codes. Its API requires a 40-character authentication key, which is stored server-side. Use OpenDART as the source for Korean corporate disclosures where available.
- On first visit, include available items from the prior 24 hours in the digest because no earlier visit watermark exists. Later visits include only records newer than the stored watermark.
- Current Finnhub search returns KRX symbols such as Samsung Electronics `005930.KS` for English-name queries; Hangul alias matching must be added.
- Finnhub documents real-time trade streaming for US equities, while its overview describes international equity market data as end-of-day. Therefore KRX watchlist entries must be labeled with their actual quote freshness/provider and must never show LIVE based on the Finnhub US trade stream. KRX live quotes require a separately configured provider such as an official Korean broker/exchange feed; do not fabricate them.

## Hosting

- Render free web service supplies the free public `onrender.com` subdomain and may sleep after inactivity.
- Neon PostgreSQL is the primary persistent DB; Cloudflare R2 provides independent archives.
- Render, Neon, Cloudflare, and OpenDART credentials must be configured through their dashboards by the user. Never request credentials in chat.
- Public deployment must remain behind single-user authentication. Before enabling actual public access, show the concrete public URL and confirm its authentication behavior with the user.

## Acceptance criteria

- Restart/sleep preserves watchlist, stored news/filings, history, and visit watermark.
- Wake sync fetches actual provider records within documented source lookback windows; digest includes items published after previous successful visit and does not advance the watermark on failure.
- Encrypted R2 archive restore reproduces the Neon records; archive quota warnings work.
- Intraday labels and crosshair use KST; date-granularity chart bars display trading dates rather than market-open times.
- Actual Finnhub US trade E2E continues through the unchanged WebSocket path.
- Korean aliases resolve to canonical KRX and international symbols. KRX data freshness is explicit and not mislabeled LIVE.
- Detailed news/SEC panel remains; separate missed-updates digest is visible.
- Public URL stays protected until user reviews the URL/access behavior.

## Known prerequisites

- Account access to Render, Neon, Cloudflare R2, and an OpenDART API key are needed for provisioning. Their credentials must be entered in provider dashboards, not shared in chat.
- The current project checkout has no `.git` directory, so deployment from a Git-linked Blueprint requires a repository to be created/connected; another supported deployment path must be selected if the user does not want to publish a repository.
- No cost is incurred automatically; stop and show current costs before selecting any paid tier or overage.
