import asyncio
import pytest

from app import providers
from app.config import settings


@pytest.fixture
def directory(monkeypatch):
    rows = [
        {"ticker": "005930.KS", "name": "삼성전자", "exchange": "KOSPI", "market": "KRX", "corp_code": "00126380"},
        {"ticker": "035420.KS", "name": "NAVER", "exchange": "KOSPI", "market": "KRX", "corp_code": "00266961"},
    ]
    async def load():
        return rows
    async def no_finnhub(query, limit=10):
        return []
    monkeypatch.setattr(providers, "_load_korean_directory", load)
    monkeypatch.setattr(providers, "_search_finnhub", no_finnhub)
    return rows


def test_hangul_name_search_returns_krx_code(directory):
    rows = asyncio.run(providers.search_stocks("삼성전자", limit=5))
    assert rows[0]["ticker"] == "005930.KS"
    assert rows[0]["market"] == "KRX"


def test_stock_code_and_suffix_search_match_korean_company(directory):
    assert asyncio.run(providers.search_stocks("005930"))[0]["ticker"] == "005930.KS"
    assert asyncio.run(providers.search_stocks("005930.KS"))[0]["name"] == "삼성전자"


def test_english_name_search_uses_directory(directory):
    rows = asyncio.run(providers.search_stocks("NAVER"))
    assert rows[0]["ticker"] == "035420.KS"


def test_missing_dart_key_degrades_without_network(monkeypatch):
    monkeypatch.setattr(settings, "opendart_api_key", "")
    providers._dart_directory_cache = None
    assert asyncio.run(providers._load_korean_directory()) == []


def test_ambiguous_hangul_alias_returns_all_exact_choices(directory):
    rows = asyncio.run(providers.search_stocks("구글"))
    assert {row["ticker"] for row in rows} == {"GOOG", "GOOGL"}


def test_krx_tickers_are_not_subscribed_to_us_trade_socket():
    from app.realtime import supports_finnhub_realtime
    assert supports_finnhub_realtime("NVDA")
    assert not supports_finnhub_realtime("005930.KS")
    assert not supports_finnhub_realtime("035420.KQ")
    assert not supports_finnhub_realtime("005930")


def test_directory_without_verified_exchange_does_not_guess_kospi_or_kosdaq(monkeypatch):
    monkeypatch.setattr(settings, "finnhub_api_key", "")
    async def load():
        return [
            {"ticker": "005930", "stock_code": "005930", "name": "삼성전자", "exchange": "KRX (시장 확인 필요)", "market": "KRX"},
            {"ticker": "123456", "stock_code": "123456", "name": "새로운회사", "exchange": "KRX (시장 확인 필요)", "market": "KRX"},
        ]
    async def no_finnhub(query, limit=10):
        return []
    monkeypatch.setattr(providers, "_load_korean_directory", load)
    monkeypatch.setattr(providers, "_search_finnhub", no_finnhub)
    rows = asyncio.run(providers.search_stocks("삼성전자"))
    assert rows[0]["ticker"] == "005930.KS"  # Curated Samsung alias has a verified symbol.
    unknown = asyncio.run(providers.search_stocks("새로운회사"))
    assert unknown[0]["ticker"] == "123456"
    assert unknown[0]["freshness"] == "UNAVAILABLE"


def test_finnhub_exchange_lookup_resolves_kosdaq_suffix(monkeypatch):
    monkeypatch.setattr(settings, "finnhub_api_key", "configured")
    async def load():
        return [{"ticker": "123456", "stock_code": "123456", "name": "테스트회사", "exchange": "KRX (시장 확인 필요)", "market": "KRX"}]
    async def find(query, limit=10):
        return [{"ticker": "123456.KQ", "name": "테스트회사", "exchange": "KOSDAQ", "market": "KRX"}] if query == "123456" else []
    monkeypatch.setattr(providers, "_load_korean_directory", load)
    monkeypatch.setattr(providers, "_search_finnhub", find)
    rows = asyncio.run(providers.search_stocks("테스트회사"))
    assert rows[0]["ticker"] == "123456.KQ"
    assert rows[0]["exchange"] == "KOSDAQ"


def test_dart_filings_use_company_code_and_official_receipt_url(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"status": "000", "list": [{"report_nm": "분기보고서", "rcept_no": "20260926000001", "rcept_dt": "20260926"}]}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): return Response()
    async def directory():
        return [{"ticker": "005930.KS", "stock_code": "005930", "corp_code": "00126380", "name": "삼성전자"}]
    monkeypatch.setattr(settings, "opendart_api_key", "not-printed")
    monkeypatch.setattr(providers, "_load_korean_directory", directory)
    monkeypatch.setattr(providers.httpx, "AsyncClient", Client)
    rows = asyncio.run(providers._fetch_dart_filings("005930.KS"))
    assert rows[0]["source"] == "OpenDART · 공시"
    assert rows[0]["url"].endswith("rcpNo=20260926000001")
    assert rows[0]["impact"] == 95
