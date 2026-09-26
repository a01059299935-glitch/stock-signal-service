from datetime import datetime, timezone
from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


class WatchItem(Base):
    __tablename__ = "watchlist"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    price: Mapped[float] = mapped_column(Float, default=0)
    change_pct: Mapped[float] = mapped_column(Float, default=0)
    previous_close: Mapped[float] = mapped_column(Float, default=0)
    volume: Mapped[int] = mapped_column(Integer, default=0)
    market_cap: Mapped[float] = mapped_column(Float, default=0)
    score: Mapped[int] = mapped_column(Integer, default=50)
    signal: Mapped[str] = mapped_column(String(20), default="HOLD")
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    reasons: Mapped[str] = mapped_column(Text, default="[]")
    risks: Mapped[str] = mapped_column(Text, default="[]")
    indicators: Mapped[str] = mapped_column(Text, default="{}")
    candles: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class NewsEvent(Base):
    __tablename__ = "news_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(120), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    sentiment: Mapped[int] = mapped_column(Integer, default=0)
    impact: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    __table_args__ = (UniqueConstraint("ticker", "fingerprint", name="uq_ticker_news_fingerprint"),)


class SignalHistory(Base):
    __tablename__ = "signal_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    score: Mapped[int] = mapped_column(Integer)
    signal: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AppState(Base):
    __tablename__ = "app_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_successful_visit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
