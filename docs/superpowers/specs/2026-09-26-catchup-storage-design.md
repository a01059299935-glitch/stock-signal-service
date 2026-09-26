# Persistent Catch-up Digest and Storage Contract

## Scope

Retain the single user's history while the Render Free web service sleeps; on return, fetch and summarize real news and filings since the last successful visit.

## Requirements

- Neon PostgreSQL is the primary durable data store, independent of Render's ephemeral service.
- Cloudflare R2 Standard stores encrypted, compressed Postgres backups. Keep periodic snapshots without automatic deletion; warn at 80% of configured free storage threshold and stop creating backups before any known overage.
- Preserve watchlist, snapshots, news/filings, signal history, and one `last_successful_visit_at` watermark.
- On wake, perform one deduplicated refresh before returning a digest labeled current. Update the visit watermark only after successful refresh and digest construction.
- Preserve old news/filings when providers omit them from later results; never prune to meet free-tier limits.
- Digest contains only source-backed items published after the previous successful visit, grouped by ticker and importance with original links. Keep the detailed news/filings list unchanged.
- On sync failure, return stored records plus the last-success timestamp and stale warning; leave the watermark untouched.
- Current catch-up source windows: Finnhub news 7 days and SEC recent filings 30 days. Report those limits. OpenDART filing lookback will be set to the supported query window and disclose it.
- A sleeping process cannot receive Finnhub trades; after wake, LIVE waits for a real new trade.

## Acceptance

- Persisted watchlist, records, signal history, and visit watermark survive app container replacement and database scale-to-zero.
- A sync failure never advances the watermark.
- R2 dump can be restored into a clean Neon-compatible Postgres database.
- No user-visible claim of completeness beyond provider lookback windows.
