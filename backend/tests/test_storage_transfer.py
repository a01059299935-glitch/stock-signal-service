from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import NewsEvent, SignalHistory, Snapshot, WatchItem
from app.storage_transfer import transfer_database


def test_transfer_moves_existing_records_from_sqlite_and_preserves_ids():
    source = create_engine("sqlite://")
    target = create_engine("sqlite://")
    Base.metadata.create_all(source)
    Base.metadata.create_all(target)
    with source.begin() as connection:
        connection.exec_driver_sql("DROP TABLE app_state")
    with Session(source) as db:
        db.add(WatchItem(id=7, ticker="NVDA"))
        db.add(NewsEvent(id=11, ticker="NVDA", fingerprint="f" * 64, title="News", source="test", url="https://example.com", summary="", sentiment=1, impact=80, published_at=datetime.now(timezone.utc)))
        db.add(Snapshot(id=15, ticker="NVDA", price=100, change_pct=1, previous_close=99, volume=10, market_cap=0, score=60, signal="BUY", confidence=55, reasons="[]", risks="[]", indicators="{}", candles="[]"))
        db.add(SignalHistory(id=19, ticker="NVDA", score=60, signal="BUY", reason="{}"))
        db.commit()
    result = transfer_database(source, target)
    assert result == {"watchlist": 1, "snapshots": 1, "news_events": 1, "signal_history": 1, "app_state": 0}
    with Session(target) as db:
        assert db.get(WatchItem, 7).ticker == "NVDA"
        assert db.get(NewsEvent, 11).fingerprint == "f" * 64
        assert db.get(Snapshot, 15).price == 100
        assert db.get(SignalHistory, 19).signal == "BUY"
    source.dispose()
    target.dispose()


def test_transfer_refuses_nonempty_target_without_deleting_anything():
    source = create_engine("sqlite://")
    target = create_engine("sqlite://")
    Base.metadata.create_all(source)
    Base.metadata.create_all(target)
    with Session(target) as db:
        db.add(WatchItem(ticker="AMD"))
        db.commit()
    with pytest.raises(ValueError, match="empty"):
        transfer_database(source, target)
    with Session(target) as db:
        assert db.scalar(select(WatchItem)).ticker == "AMD"
    source.dispose()
    target.dispose()
