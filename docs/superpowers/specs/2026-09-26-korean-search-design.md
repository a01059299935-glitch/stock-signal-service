# Korean Search and KRX Listings Contract

## Scope

Allow lookup of KRX-listed companies by Korean corporate name and of supported international equities by Korean alias, English name, or ticker.

## Requirements

- Use OpenDART corporation directory data (`corp_name`, six-digit `stock_code`) as the authoritative KRX Korean-name-to-code mapping; fetch/cache it on the server and require `OPENDART_API_KEY` only in server settings.
- Use ticker suffix `.KS`/`.KQ` for KRX market identity and show exchange alongside the code.
- Search and add KRX names/tickers even when real-time trade support is absent; never mark KRX price status LIVE based on the Finnhub US WebSocket.
- Support KRX filings from OpenDART when the key is configured; existing SEC EDGAR workflow stays for US filers.
- Use a small curated alias map for widely used Korean names of US/global companies, with exact ticker matching and ambiguity choices. No fuzzy result may silently add a guessed ticker.
- Clearly display actual source and freshness for KRX prices. Finnhub international data availability/entitlement must be verified per symbol; unsupported live values remain unavailable or delayed.

## Acceptance

- Hangul, English, and ticker queries find Samsung Electronics `005930.KS` and a representative supported US ticker.
- Missing/invalid DART key degrades search/filings gracefully and never exposes the key.
- A KRX ticker is never shown as LIVE without an actual trade event from a provider supporting that KRX market.
