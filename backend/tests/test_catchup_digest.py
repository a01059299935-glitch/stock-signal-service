import asyncio
from datetime import datetime, timedelta, timezone

from app.digest import build_missed_digest, run_catchup_digest
from app.tasks import persist_news_events
from app.models import AppState, NewsEvent
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker


def record(ticker, fingerprint, published_at, impact=70):
    return {"ticker": ticker, "fingerprint": fingerprint, "published_at": published_at, "title": fingerprint, "source": "provider", "summary": "summary", "url": "https://example.com", "impact": impact}


def test_first_visit_uses_last_24_hours_and_groups_by_ticker_and_impact():
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    rows = [record("NVDA", "new", now - timedelta(hours=1)), record("AMD", "old", now - timedelta(days=2))]
    digest = build_missed_digest(rows, now - timedelta(hours=24))
    assert [(group["ticker"], group["impact"]) for group in digest] == [("NVDA", 70)]


def test_digest_deduplicates_source_fingerprints():
    now = datetime.now(timezone.utc)
    rows = [record("NVDA", "same", now), record("NVDA", "same", now)]
    digest = build_missed_digest(rows, now - timedelta(days=1))
    assert len(digest) == 1 and len(digest[0]["items"]) == 1


def test_success_advances_watermark_after_building_new_items():
    now = datetime.now(timezone.utc)
    watermark = now - timedelta(days=1)
    saved = []
    async def run():
        return await run_catchup_digest(lambda: {"failed": []}, lambda: [record("NVDA", "new", now)], lambda: watermark, saved.append, now)
    result = asyncio.run(run())
    assert result["stale"] is False
    assert result["items"][0]["items"][0]["fingerprint"] == "new"
    assert saved == [now]


def test_partial_provider_failure_keeps_previous_watermark():
    now = datetime.now(timezone.utc)
    watermark = now - timedelta(days=1)
    saved = []
    async def run():
        return await run_catchup_digest(lambda: {"failed": ["AMD"]}, lambda: [record("NVDA", "new", now)], lambda: watermark, saved.append, now)
    result = asyncio.run(run())
    assert result["stale"] is True
    assert result["synced_at"] == watermark.isoformat()
    assert saved == []


def test_provider_omission_does_not_delete_previously_saved_records():
    engine = create_engine("sqlite://")
    NewsEvent.__table__.create(engine)
    with Session(engine) as db:
        persist_news_events(db, "NVDA", [record("NVDA", "old", datetime.now(timezone.utc))])
        db.commit()
        assert persist_news_events(db, "NVDA", []) == []
        db.commit()
        assert db.scalar(select(NewsEvent).where(NewsEvent.fingerprint == "old")) is not None
    engine.dispose()


def test_digest_endpoint_coalesces_concurrent_wake_requests(monkeypatch):
    from app import main
    from app.db import Base
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", factory)
    calls = []
    async def refresh():
        calls.append(1)
        await asyncio.sleep(0.03)
        return {"failed": []}
    monkeypatch.setattr(main, "refresh_watchlist", refresh)
    main._digest_task = None
    now = datetime.now(timezone.utc)
    with factory() as db:
        db.add(NewsEvent(ticker="NVDA", fingerprint="wake-news", title="Wake", source="test", url="https://example.com", summary="", sentiment=0, impact=80, published_at=now))
        db.commit()
    async def requests():
        return await asyncio.gather(main.digest_refresh(), main.digest_refresh())
    results = asyncio.run(requests())
    assert len(calls) == 1
    assert results[0] == results[1]
    with factory() as db:
        state = db.get(AppState, 1)
        assert state.last_successful_visit_at is not None
    main._digest_task = None
    engine.dispose()
