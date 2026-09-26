import hashlib
import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import httpx
from functools import lru_cache
from .config import settings

BASE = "https://finnhub.io/api/v1"
_sec_tickers: dict[str, int] | None = None
_company_names: dict[str, str] = {}
_listing_cache: tuple[datetime, list[dict]] | None = None
_provider_cache: dict[tuple, tuple[datetime, object]] = {}
_dart_directory_cache: tuple[datetime, list[dict]] | None = None
logger = logging.getLogger(__name__)

_KOREAN_ALIASES = {
    "애플": [("AAPL", "Apple Inc.")], "엔비디아": [("NVDA", "NVIDIA Corporation")], "엔비디아 주식": [("NVDA", "NVIDIA Corporation")],
    "테슬라": [("TSLA", "Tesla, Inc.")], "마이크로소프트": [("MSFT", "Microsoft Corporation")], "마소": [("MSFT", "Microsoft Corporation")],
    "구글": [("GOOG", "Alphabet Inc. Class C"), ("GOOGL", "Alphabet Inc. Class A")], "알파벳": [("GOOG", "Alphabet Inc. Class C"), ("GOOGL", "Alphabet Inc. Class A")],
    "아마존": [("AMZN", "Amazon.com, Inc.")], "메타": [("META", "Meta Platforms, Inc.")], "브로드컴": [("AVGO", "Broadcom Inc.")],
    "삼성전자": [("005930.KS", "삼성전자")], "삼성전자우": [("005935.KS", "삼성전자우")],
}


def _cached(key, ttl_seconds):
    item = _provider_cache.get(key)
    return item[1] if item and (datetime.now(timezone.utc) - item[0]).total_seconds() < ttl_seconds else None


def _cache(key, value):
    _provider_cache[key] = (datetime.now(timezone.utc), value)
    return value


async def _load_korean_directory() -> list[dict]:
    """Download OpenDART's official listed-company code directory, cached for a day."""
    global _dart_directory_cache
    now = datetime.now(timezone.utc)
    if _dart_directory_cache and (now - _dart_directory_cache[0]).total_seconds() < 86400:
        return _dart_directory_cache[1]
    if not settings.opendart_api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": settings.opendart_api_key})
            response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            xml_name = next(name for name in archive.namelist() if name.lower().endswith(".xml"))
            root = ET.fromstring(archive.read(xml_name))
        rows = []
        for corp in root.findall("list"):
            code = (corp.findtext("stock_code") or "").strip()
            name = (corp.findtext("corp_name") or "").strip()
            corp_code = (corp.findtext("corp_code") or "").strip()
            if not re.fullmatch(r"\d{6}", code) or not name or not corp_code:
                continue
            english = (corp.findtext("corp_eng_name") or "").strip()
            rows.append({"ticker": code, "stock_code": code, "corp_code": corp_code, "name": name, "english_name": english, "exchange": "KRX (시장 확인 필요)", "market": "KRX"})
        _dart_directory_cache = (now, rows)
        return rows
    except Exception as exc:
        logger.warning("OpenDART company directory unavailable (%s)", type(exc).__name__)
        return _dart_directory_cache[1] if _dart_directory_cache else []


async def _search_finnhub(query: str, limit: int = 10) -> list[dict]:
    if not settings.finnhub_api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f"{BASE}/search", params={"q": query}, headers={"X-Finnhub-Token": settings.finnhub_api_key})
            response.raise_for_status()
        rows = []
        for row in response.json().get("result", []):
            symbol = row.get("symbol")
            if not symbol:
                continue
            is_krx = str(symbol).upper().endswith((".KS", ".KQ"))
            rows.append({"ticker": str(symbol).upper(), "name": row.get("description", symbol), "exchange": "KOSPI" if str(symbol).upper().endswith(".KS") else "KOSDAQ" if str(symbol).upper().endswith(".KQ") else row.get("type", "US"), "market": "KRX" if is_krx else "US"})
        return rows[:limit]
    except Exception:
        logger.exception("Finnhub symbol search failed")
        return []


async def _fetch_dart_filings(ticker: str) -> list[dict]:
    if not settings.opendart_api_key or not re.fullmatch(r"\d{6}(?:\.K[QS])?", ticker.upper()):
        return []
    code = ticker.split(".", 1)[0]
    directory = await _load_korean_directory()
    company = next((row for row in directory if row.get("stock_code") == code or row["ticker"] == ticker.upper()), None)
    if not company:
        return []
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=90)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get("https://opendart.fss.or.kr/api/list.json", params={"crtfc_key": settings.opendart_api_key, "corp_code": company["corp_code"], "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"), "page_no": 1, "page_count": 100, "sort": "date", "sort_mth": "desc"})
            response.raise_for_status()
        payload = response.json()
        if payload.get("status") not in {"000", "013"}:
            logger.warning("OpenDART filing lookup unavailable for %s (status=%s)", ticker, payload.get("status"))
            return []
        out = []
        for filing in payload.get("list", []):
            title = str(filing.get("report_nm", "")).strip()
            if not title:
                continue
            important = any(term in title for term in ("사업보고서", "분기보고서", "반기보고서", "주요사항"))
            impact = 95 if important else 65 if "소유상황" in title or "임원" in title else 55
            receipt = str(filing.get("rcept_no", ""))
            date = str(filing.get("rcept_dt", ""))
            if not receipt or not re.fullmatch(r"\d{8}", date):
                continue
            fingerprint = hashlib.sha256(f"DART:{receipt}".encode()).hexdigest()
            out.append({"title": f"DART 공시 · {title} — {company['name']}", "source": "OpenDART · 공시", "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}", "summary": "", "sentiment": 0, "impact": impact, "fingerprint": fingerprint, "published_at": datetime.strptime(date, "%Y%m%d").replace(tzinfo=timezone.utc)})
        return out
    except Exception as exc:
        logger.warning("OpenDART filings unavailable for %s (%s)", ticker, type(exc).__name__)
        return []


def to_chart_time(timestamp_seconds: int, timeframe: str) -> int | str:
    """Keep intraday candles as instants and coarser candles as US session dates."""
    if timeframe not in {"1m", "5m", "15m", "1h", "1D", "1W", "1M"}:
        raise ValueError("지원하지 않는 캔들 간격입니다.")
    if timeframe in {"1m", "5m", "15m", "1h"}:
        return int(timestamp_seconds)
    local_date = datetime.fromtimestamp(int(timestamp_seconds), ZoneInfo("America/New_York")).date()
    if timeframe == "1W":
        local_date -= timedelta(days=local_date.weekday())
    elif timeframe == "1M":
        local_date = local_date.replace(day=1)
    return local_date.isoformat()


def _normalize_bars(timestamps, quote):
    bars = []
    for stamp, open_, high, low, close, volume in zip(timestamps, quote.get("open", []), quote.get("high", []), quote.get("low", []), quote.get("close", []), quote.get("volume", [])):
        if None not in (stamp, open_, high, low, close):
            bars.append({"time": int(stamp), "open": float(open_), "high": float(high), "low": float(low), "close": float(close), "volume": int(volume or 0)})
    return bars


def _aggregate_bars(bars, timeframe):
    seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}.get(timeframe)
    result = {}
    for item in bars:
        if timeframe in {"1D", "1W", "1M"}:
            stamp = to_chart_time(item["time"], timeframe)
        elif seconds:
            stamp = item["time"] // seconds * seconds
        else:
            return bars
        bar = result.get(stamp)
        if bar is None:
            result[stamp] = {"time": stamp, "open": item["open"], "high": item["high"], "low": item["low"], "close": item["close"], "volume": item["volume"]}
        else:
            bar["high"] = max(bar["high"], item["high"])
            bar["low"] = min(bar["low"], item["low"])
            bar["close"] = item["close"]
            bar["volume"] += item["volume"]
    return list(result.values())


async def fetch_candles(ticker: str, timeframe: str = "1m", chart_range: str = "RT") -> dict:
    ticker, chart_range = ticker.upper(), chart_range.upper()
    if chart_range not in {"RT", "1D", "1W", "3M", "1Y", "5Y", "ALL"}:
        raise ValueError("지원하지 않는 차트 기간입니다.")
    if timeframe not in {"1m", "5m", "15m", "1h", "1D"}:
        raise ValueError("지원하지 않는 캔들 간격입니다.")
    key = ("candles", ticker, timeframe, chart_range)
    cached = _cached(key, 300)
    if cached is not None:
        return cached
    periods = {"RT": "1d", "1D": "1d", "1W": "5d", "3M": "3mo", "1Y": "1y", "5Y": "5y", "ALL": "max"}
    if chart_range in {"3M", "1Y"}:
        interval, actual = "1d", "1D"
    elif chart_range == "5Y":
        interval, actual = "1wk", "1W"
    elif chart_range == "ALL":
        interval, actual = "1mo", "1M"
    else:
        interval = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "1D": "1m"}[timeframe]
        actual = timeframe
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
            response = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}", params={"range": periods[chart_range], "interval": interval})
            response.raise_for_status()
            result = response.json()["chart"]["result"][0]
        bars = _normalize_bars(result.get("timestamp", []), result["indicators"]["quote"][0])
        if chart_range in {"RT", "1D", "1W"}:
            bars = _aggregate_bars(bars, timeframe)
        elif actual in {"1W", "1M"}:
            bars = _aggregate_bars(bars, actual)
        return _cache(key, {"candles": bars, "source": "Yahoo Finance", "range": chart_range, "timeframe": actual, "requested_timeframe": timeframe, "updated_at": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        logger.warning("Historical candles unavailable for %s (%s)", ticker, type(exc).__name__)
        return {"candles": [], "source": None, "range": chart_range, "timeframe": actual, "requested_timeframe": timeframe, "updated_at": None}


async def fetch_stock(ticker: str) -> tuple[dict, list[dict]]:
    headers = {"X-Finnhub-Token": settings.finnhub_api_key} if settings.finnhub_api_key else {}
    quote = {}
    if settings.finnhub_api_key:
        async with httpx.AsyncClient(timeout=12) as client:
            try:
                response = await client.get(f"{BASE}/quote", params={"symbol": ticker}, headers=headers)
                response.raise_for_status()
                quote = response.json()
            except Exception:
                logger.warning("Finnhub quote unavailable for %s", ticker)
    bars = (await fetch_candles(ticker, "1D", "1Y"))["candles"]
    if quote.get("c"):
        profile = {}
        if settings.finnhub_api_key:
            async with httpx.AsyncClient(timeout=12) as client:
                try:
                    response = await client.get(f"{BASE}/stock/profile2", params={"symbol": ticker}, headers=headers)
                    if response.is_success:
                        profile = response.json()
                except Exception:
                    logger.warning("Finnhub profile unavailable for %s", ticker)
        price = float(quote["c"])
        change = float(quote.get("dp", 0))
        previous_close = float(quote.get("pc") or (price / (1 + change / 100) if change != -100 else price))
        return {"price": price, "change_pct": change, "previous_close": previous_close, "volume": bars[-1]["volume"] if bars else 0, "market_cap": float(profile.get("marketCapitalization") or 0) * 1_000_000}, bars
    if not bars:
        raise RuntimeError(f"No historical price data available for {ticker}")
    price, previous = bars[-1]["close"], bars[-2]["close"] if len(bars) > 1 else bars[-1]["close"]
    return {"price": price, "change_pct": (price / previous - 1) * 100 if previous else 0, "previous_close": previous, "volume": bars[-1]["volume"], "market_cap": 0}, bars


async def fetch_fundamentals(ticker: str) -> dict:
    key = ("fundamentals", ticker.upper())
    cached = _cached(key, 21600)
    if cached is not None:
        return cached
    if not settings.finnhub_api_key:
        return {}
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.get(f"{BASE}/stock/metric", params={"symbol": ticker, "metric": "all"}, headers={"X-Finnhub-Token": settings.finnhub_api_key})
        response.raise_for_status()
    raw = response.json().get("metric", {})
    names = ("peBasicExclExtraTTM", "epsTTM", "grossMarginTTM", "operatingMarginTTM", "netProfitMarginTTM", "revenuePerShareTTM", "beta", "52WeekHigh", "52WeekLow")
    metrics = {name: raw[name] for name in names if isinstance(raw.get(name), (int, float))}
    subscores = []
    for name in ("grossMarginTTM", "operatingMarginTTM", "netProfitMarginTTM"):
        value = metrics.get(name)
        if value is not None:
            subscores.append(25 if value < 0 else 40 if value < 10 else 55 if value < 20 else 68 if value < 40 else 80)
    pe = metrics.get("peBasicExclExtraTTM")
    if pe is not None and pe > 0:
        subscores.append(75 if pe < 15 else 68 if pe < 25 else 58 if pe < 40 else 48 if pe < 60 else 35)
    eps = metrics.get("epsTTM")
    if eps is not None:
        subscores.append(65 if eps > 0 else 25)
    if subscores:
        metrics["score"] = round(sum(subscores) / len(subscores))
    return _cache(key, metrics)


async def fetch_market_context() -> dict:
    key = ("market", "SPY")
    cached = _cached(key, 300)
    if cached is not None:
        return cached
    if not settings.finnhub_api_key:
        return {}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{BASE}/quote", params={"symbol": "SPY"}, headers={"X-Finnhub-Token": settings.finnhub_api_key})
        response.raise_for_status()
    quote = response.json()
    change = float(quote.get("dp") or 0)
    return _cache(key, {"symbol": "SPY", "change_pct": change, "score": round(max(0, min(100, 50 + change * 4))), "price": float(quote.get("c") or 0)})


async def fetch_event_risk(ticker: str, news: list[dict]) -> dict:
    key = ("earnings", ticker.upper())
    cached = _cached(key, 21600)
    if cached is not None:
        earnings = cached
    elif settings.finnhub_api_key:
        start = datetime.now(timezone.utc).date()
        end = start + timedelta(days=90)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{BASE}/calendar/earnings", params={"symbol": ticker, "from": start.isoformat(), "to": end.isoformat()}, headers={"X-Finnhub-Token": settings.finnhub_api_key})
                response.raise_for_status()
            dates = sorted(row["date"] for row in response.json().get("earningsCalendar", []) if row.get("date"))
            earnings = _cache(key, dates)
        except Exception:
            logger.warning("Earnings calendar unavailable for %s", ticker)
            earnings = None
    else:
        earnings = None
    days = (datetime.fromisoformat(earnings[0]).date() - datetime.now(timezone.utc).date()).days if earnings else None
    score = 50 if earnings is None else 75 if days is None else 35 if days <= 2 else 45 if days <= 7 else 60 if days <= 14 else 75
    label = f"실적 발표까지 {days}일" if days is not None else "90일 내 예정 실적 없음" if earnings == [] else "실적 일정 데이터 미연결"
    material = [item for item in news if "SEC EDGAR" in item.get("source", "") and any(form in item.get("source", "") for form in ("8-K", "10-K", "10-Q"))]
    if material and material[0].get("published_at") and (datetime.now(timezone.utc) - material[0]["published_at"]).days <= 3:
        score = min(score, 40)
        label += " · 최근 중요 공시"
    return {"score": score, "label": label, "next_earnings": earnings[0] if earnings else None, "days_to_earnings": days}


async def fetch_news(ticker: str) -> list[dict]:
    global _sec_tickers, _company_names
    out = []
    if re.fullmatch(r"\d{6}(?:\.K[QS])?", ticker.upper()):
        out.extend(await _fetch_dart_filings(ticker))
    if _sec_tickers is None:
        try:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}) as client:
                index = await client.get("https://www.sec.gov/files/company_tickers.json")
                index.raise_for_status()
                companies = list(index.json().values())
            _sec_tickers = {str(v["ticker"]).upper(): int(v["cik_str"]) for v in companies}
            _company_names = {str(v["ticker"]).upper(): str(v.get("title", "")) for v in companies}
        except Exception:
            logger.exception("SEC company directory unavailable; news matching may be broader")
            _sec_tickers = {}
    if settings.finnhub_api_key:
        start = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
        end = datetime.now(timezone.utc).date().isoformat()
        async with httpx.AsyncClient(timeout=12) as client:
            try:
                response = await client.get(f"{BASE}/company-news", params={"symbol": ticker, "from": start, "to": end}, headers={"X-Finnhub-Token": settings.finnhub_api_key})
                response.raise_for_status(); articles = response.json()
            except Exception:
                logger.exception("Finnhub news failed for %s; continuing with SEC filings", ticker)
                articles = []
    else: articles = []
    for a in articles:
        title = str(a.get("headline", "")).strip()
        if not title: continue
        summary = str(a.get("summary", "")).strip()
        name = _company_names.get(ticker.upper(), "")
        tokens = [x for x in re.findall(r"[a-z0-9]+", name.lower()) if x not in {"inc", "corp", "corporation", "company", "co", "ltd", "plc", "the"} and len(x) > 2]
        # Relevance must be established by the headline; a passing mention in a broad
        # article body can otherwise make unrelated sector or crypto stories look ticker-specific.
        headline = title.lower()
        if tokens and not any(re.search(rf"\b{re.escape(token)}\b", headline) for token in tokens) and not re.search(rf"\b{re.escape(ticker.lower())}\b", headline):
            continue
        words = set(re.findall(r"[a-z]+", title.lower()))
        pos = {"beats", "growth", "record", "upgrade", "surge", "raises", "wins", "deal", "profit", "approval"}
        neg = {"misses", "cuts", "downgrade", "lawsuit", "probe", "recall", "falls", "decline", "loss", "delay"}
        sentiment = max(-80, min(80, (len(words & pos) - len(words & neg)) * 25))
        published = datetime.fromtimestamp(int(a["datetime"]), timezone.utc) if a.get("datetime") else None
        out.append({"title": title, "source": a.get("source", ""), "url": a.get("url", ""), "summary": summary, "sentiment": sentiment, "impact": min(95, 35 + len(words & (pos | neg)) * 25), "fingerprint": "", "published_at": published})
    # SEC submissions are primary-source disclosures and complement news-provider coverage.
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}) as client:
        cik = _sec_tickers.get(ticker.upper())
        if cik:
            try:
                filing = await client.get(f"https://data.sec.gov/submissions/CIK{cik:010}.json"); filing.raise_for_status()
                recent = filing.json().get("filings", {}).get("recent", {})
                forms = recent.get("form", []); dates = recent.get("filingDate", []); accessions = recent.get("accessionNumber", []); docs = recent.get("primaryDocument", [])
                cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
                for form, date, accession, doc in zip(forms, dates, accessions, docs):
                    if date < cutoff or form not in {"8-K", "10-K", "10-Q", "4", "S-3", "13D", "13G"}: continue
                    accession_path = accession.replace("-", "")
                    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_path}/{doc}"
                    title = f"SEC 공시 {form} 제출 — {ticker} ({date})"
                    fingerprint = hashlib.sha256(f"{ticker}:{accession}".encode()).hexdigest()
                    out.append({"title": title, "source": f"SEC EDGAR · {form}", "url": url, "summary": "", "sentiment": 0, "impact": 95 if form in {"8-K", "10-K", "10-Q"} else 65 if form == "4" else 55, "fingerprint": fingerprint, "published_at": datetime.fromisoformat(date).replace(tzinfo=timezone.utc)})
            except Exception:
                pass
    unique = []
    for article in sorted(out, key=lambda item: ("SEC EDGAR" in item["source"], item["published_at"] or datetime.min.replace(tzinfo=timezone.utc), item["impact"]), reverse=True):
        normalized = re.sub(r"[^a-z0-9]+", " ", article["title"].lower()).strip()
        if not article["source"].startswith("SEC EDGAR") and any(not prior["source"].startswith("SEC EDGAR") and SequenceMatcher(None, normalized, re.sub(r"[^a-z0-9]+", " ", prior["title"].lower()).strip()).ratio() >= .88 for prior in unique):
            continue
        article["fingerprint"] = article["fingerprint"] or hashlib.sha256(normalized.encode()).hexdigest()
        unique.append(article)
        if len(unique) == 60:
            break
    return unique


async def search_stocks(query: str, limit: int = 10) -> list[dict]:
    global _listing_cache
    raw = query.strip()
    q = raw.upper()
    if not raw:
        return []
    results = await _search_finnhub(raw, limit)
    by_ticker = {row["ticker"].upper(): row for row in results}
    alias_matches = []
    for alias, companies in _KOREAN_ALIASES.items():
        if raw.casefold() in alias.casefold() or alias.casefold() in raw.casefold():
            alias_matches.extend({"ticker": ticker, "name": name, "exchange": "US" if not ticker.endswith((".KS", ".KQ")) else "KRX", "market": "US" if not ticker.endswith((".KS", ".KQ")) else "KRX"} for ticker, name in companies)
    for row in alias_matches:
        by_ticker.setdefault(row["ticker"].upper(), row)

    directory = await _load_korean_directory()
    code_query = re.sub(r"\.(KS|KQ)$", "", q)
    krx_matches = [row for row in directory if (code_query and code_query in row.get("ticker", "").upper().split(".")[0]) or raw.casefold() in row.get("name", "").casefold() or raw.casefold() in row.get("english_name", "").casefold()]
    for row in krx_matches:
        ticker = row["ticker"].upper()
        code = row.get("stock_code") or ticker.split(".", 1)[0]
        explicit_suffix = q.split(".", 1)[1] if q.endswith((".KS", ".KQ")) else ""
        curated = next((item["ticker"] for item in alias_matches if item["ticker"].split(".", 1)[0] == code and item["ticker"].endswith((".KS", ".KQ"))), None)
        if not ticker.endswith((".KS", ".KQ")):
            ticker = f"{code}.{explicit_suffix}" if explicit_suffix else curated or ticker
        # Finnhub's symbol lookup identifies the KOSPI/KOSDAQ suffix where entitled.
        if settings.finnhub_api_key:
            code_matches = await _search_finnhub(code, 20)
            resolved = next((item for item in code_matches if item["ticker"].split(".", 1)[0] == code and item["ticker"].endswith((".KS", ".KQ"))), None)
            if resolved:
                ticker = resolved["ticker"]
                row = {**row, "exchange": resolved["exchange"]}
        freshness = "EOD" if ticker.endswith((".KS", ".KQ")) else "UNAVAILABLE"
        normalized = {"ticker": ticker, "name": row.get("name", ticker), "exchange": row.get("exchange", "KRX"), "market": "KRX", "freshness": freshness}
        if raw.casefold() in normalized["name"].casefold() or (code_query and (code_query in code or code_query in ticker)):
            by_ticker[ticker] = normalized

    now = datetime.now(timezone.utc)
    if _listing_cache is None or (now - _listing_cache[0]).total_seconds() > 86400:
        try:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}) as client:
                response = await client.get("https://www.sec.gov/files/company_tickers_exchange.json")
                response.raise_for_status(); payload = response.json()
            records = payload.get("data", [])
            _listing_cache = (now, [{"ticker": str(row[2]).upper(), "name": str(row[1]), "exchange": str(row[3] or "US")} for row in records if len(row) > 3 and row[2] and row[1]])
        except Exception:
            logger.exception("SEC ticker directory unavailable")
    if _listing_cache:
        for row in _listing_cache[1]:
            if q in row["ticker"] or raw.casefold() in row["name"].casefold():
                by_ticker.setdefault(row["ticker"], {**row, "market": "US"})
    matches = list(by_ticker.values())
    matches.sort(key=lambda x: (not x["ticker"].startswith(q), not x["name"].casefold().startswith(raw.casefold()), x["ticker"]))
    return matches[:limit]


async def send_telegram(message: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise RuntimeError("Telegram Bot Token과 Chat ID를 .env에 설정해야 합니다.")
    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage", json={"chat_id": settings.telegram_chat_id, "text": message, "disable_web_page_preview": True})
        r.raise_for_status()
        if not r.json().get("ok"): raise RuntimeError("Telegram 메시지 전송에 실패했습니다.")
