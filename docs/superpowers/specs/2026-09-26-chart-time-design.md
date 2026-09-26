# Chart Time and Range Contract

## Scope

Correct time semantics in the existing Lightweight Charts view without changing the Finnhub real-trade pipeline.

## Requirements

- Intraday candle times remain Unix seconds and are formatted in Asia/Seoul; chart labels say KST.
- Daily, weekly, and monthly candles use Lightweight Charts business-date values, not market-open Unix timestamps.
- Period controls remain RT, 1D, 1W, 3M, 1Y, 5Y, ALL and show a truthful effective candle interval.
- Tooltips show date/time appropriate to the candle granularity and KST for intraday values.
- Keep a stable chart/series instance; live events update the current candle only.
- US exchange session bucketing uses America/New_York in the backend. Display timezone is Asia/Seoul.

## Acceptance

- Date-granularity candles display trading dates, never labels like 22:30 as the session date.
- Intraday axis and tooltip render known Unix timestamps as the correct Seoul time.
- RT live candle updates remain based on actual Finnhub trades.
- 1W/5Y endpoint errors are visible and handled without silently showing an empty or stale chart as current.
