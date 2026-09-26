import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import desc, select

from .analysis import dump, indicators, score_snapshot, trade_levels
from .config import settings
from .db import SessionLocal
from .models import NewsEvent, SignalHistory, Snapshot, WatchItem
from .providers import fetch_event_risk, fetch_fundamentals, fetch_market_context, fetch_news, fetch_stock, send_telegram

logger = logging.getLogger(__name__)


def persist_news_events(db, ticker: str, articles: list[dict]) -> list[dict]:
    fresh = []
    for article in articles:
        existing = db.scalar(select(NewsEvent).where(NewsEvent.ticker == ticker, NewsEvent.fingerprint == article["fingerprint"]))
        if existing:
            for field, value in article.items():
                if field != "ticker":
                    setattr(existing, field, value)
        else:
            db.add(NewsEvent(ticker=ticker, **{key: value for key, value in article.items() if key != "ticker"}))
            fresh.append(article)
    db.flush()
    return fresh


async def refresh_watchlist():
    with SessionLocal() as db:
        symbols = [item.ticker for item in db.scalars(select(WatchItem)).all()]
    failed = []
    for ticker in symbols:
        try:
            await refresh_ticker(ticker)
        except Exception:
            failed.append(ticker)
            logger.exception("Refresh failed for %s", ticker)
    return {"tickers": len(symbols), "failed": failed}


async def refresh_ticker(ticker: str):
    quote, bars = await fetch_stock(ticker)
    articles = await fetch_news(ticker)
    ind = indicators([x["close"] for x in bars], [x["volume"] for x in bars], bars)
    fundamentals, market = await asyncio.gather(fetch_fundamentals(ticker), fetch_market_context())
    ind["fundamentals"] = fundamentals
    ind["market"] = market
    ind["event_risk"] = await fetch_event_risk(ticker, articles)
    alerts = []
    with SessionLocal() as db:
        fresh = persist_news_events(db, ticker, articles)
        recent = db.scalars(select(NewsEvent).where(NewsEvent.ticker == ticker).order_by(desc(NewsEvent.published_at), desc(NewsEvent.created_at)).limit(20)).all()
        news = [{"sentiment": n.sentiment, "impact": n.impact, "published_at": n.published_at} for n in recent]
        score, signal, confidence, reasons, risks = score_snapshot(quote["price"], quote["change_pct"], news, ind)
        previous = db.scalar(select(Snapshot).where(Snapshot.ticker == ticker).order_by(desc(Snapshot.updated_at)).limit(1))
        db.add(Snapshot(ticker=ticker, **quote, score=score, signal=signal, confidence=confidence, reasons=dump(reasons), risks=dump(risks), indicators=dump(ind), candles=dump(bars)))
        if not previous or previous.signal != signal:
            previous_signal = previous.signal if previous else None
            previous_score = previous.score if previous else None
            db.add(SignalHistory(ticker=ticker, score=score, signal=signal, reason=dump({"previous_signal": previous_signal, "previous_score": previous_score, "evidence": reasons})))
            if previous and (previous.signal, signal) in {("HOLD", "BUY"), ("BUY", "STRONG BUY"), ("HOLD", "SELL"), ("SELL", "STRONG SELL")}:
                alerts.append(f"📊 {ticker} 신호 변경\n{previous.signal} → {signal}\n점수 {previous.score} → {score}/100\n현재가 ${quote['price']:.2f}\n\n근거\n" + "\n".join(f"• {r}" for r in reasons) + "\n\n위험\n" + ("\n".join(f"• {r}" for r in risks) or "• 확인된 위험요인 없음"))
        if previous and previous.price:
            move = (quote["price"] / previous.price - 1) * 100
            if abs(move) >= settings.alert_move_10m_pct:
                alerts.append(f"⚡ {ticker} 단기 급등락 감지\n최근 갱신 대비 {move:+.2f}%\n현재가 ${quote['price']:.2f}\n신호 {signal} · 점수 {score}/100")
            old_ind = json.loads(previous.indicators)
            for key, label, rising in [("high_20", "최근 고점 저항 돌파", True), ("low_20", "최근 저점 지지 하향 이탈", False)]:
                level = old_ind.get(key)
                crossed = level and ((previous.price <= level < quote["price"]) if rising else (previous.price >= level > quote["price"]))
                if crossed:
                    alerts.append(f"📍 {ticker} {label}\n기준 ${level:.2f} · 현재가 ${quote['price']:.2f}\n신호 {signal} · 점수 {score}/100")
        for article in fresh:
            if article["impact"] >= 80:
                alerts.append(f"🚨 {ticker} 중요 뉴스\n{article['title']}\n영향도 {article['impact']} · 감성 {article['sentiment']:+}\n현재가 ${quote['price']:.2f}\n{article['url']}")
        db.commit()
    for alert in alerts:
        try:
            await send_telegram(alert)
        except Exception:
            logger.exception("Telegram alert failed for %s", ticker)


async def analyze_live_trade(trade, market=None):
    """Re-evaluate the current rule signal from actual trade events."""
    alert = None
    with SessionLocal() as db:
        ticker = trade["symbol"]
        item = db.scalar(select(WatchItem).where(WatchItem.ticker == ticker))
        snapshot = db.scalar(select(Snapshot).where(Snapshot.ticker == ticker).order_by(desc(Snapshot.updated_at)).limit(1))
        if not item or not snapshot or not snapshot.previous_close:
            return
        change = (trade["price"] / snapshot.previous_close - 1) * 100
        ind = json.loads(snapshot.indicators)
        recent = db.scalars(select(NewsEvent).where(NewsEvent.ticker == ticker).order_by(desc(NewsEvent.published_at), desc(NewsEvent.created_at)).limit(20)).all()
        news = [{"sentiment": n.sentiment, "impact": n.impact, "published_at": n.published_at} for n in recent]
        score, signal, confidence, reasons, risks = score_snapshot(trade["price"], change, news, ind)
        previous_signal = snapshot.signal
        previous_score = snapshot.score
        events = []
        old_price = trade.get("previous_price")
        if old_price is not None:
            support, resistance = ind.get("low_20"), ind.get("high_20")
            invalidation = trade_levels(trade["price"], ind).get("invalidation")
            if support and old_price >= support > trade["price"]: events.append(("SUPPORT_BREAKDOWN", f"지지선 ${support:.2f} 하향 이탈"))
            if resistance and old_price <= resistance < trade["price"]: events.append(("RESISTANCE_BREAKOUT", f"저항선 ${resistance:.2f} 상향 돌파"))
            if invalidation and old_price > invalidation >= trade["price"]: events.append(("INVALIDATION_REACHED", f"무효화선 ${invalidation:.2f} 도달"))
        bar1 = trade.get("bars", {}).get("1m")
        if bar1 and bar1["open"] and abs((trade["price"] / bar1["open"] - 1) * 100) >= 1:
            events.append(("1M_RAPID_MOVE", f"1분 변동 {((trade['price'] / bar1['open'] - 1) * 100):+.2f}%"))
        bar5 = trade.get("bars", {}).get("5m")
        if bar5 and bar5["open"] and abs((trade["price"] / bar5["open"] - 1) * 100) >= 2:
            events.append(("5M_RAPID_MOVE", f"5분 변동 {((trade['price'] / bar5['open'] - 1) * 100):+.2f}%"))
        avg_daily = ind.get("volume_avg20") or 0
        previous_minute = trade.get("previous_bars", {}).get("1m")
        if bar1 and avg_daily and bar1["volume"] >= avg_daily / 390 * 3 and (not previous_minute or previous_minute.get("volume", 0) < avg_daily / 390 * 3):
            events.append(("ABNORMAL_VOLUME", "1분 거래량이 20일 일평균 분당 거래량의 3배 초과"))
        emitted = [label for event, label in events if not market or market.should_emit(ticker, event)]
        snapshot.price = trade["price"]
        snapshot.change_pct = change
        snapshot.score = score
        snapshot.signal = signal
        snapshot.confidence = confidence
        snapshot.reasons = dump(reasons)
        snapshot.risks = dump(risks)
        snapshot.updated_at = datetime.now(timezone.utc)
        if previous_signal != signal:
            db.add(SignalHistory(ticker=ticker, score=score, signal=signal, reason=dump({"previous_signal": previous_signal, "previous_score": previous_score, "evidence": reasons})))
            if (previous_signal, signal) in {("HOLD", "BUY"), ("BUY", "STRONG BUY"), ("HOLD", "SELL"), ("SELL", "STRONG SELL")}:
                alert = f"📊 {ticker} 실시간 신호 변경\n{previous_signal} → {signal}\n점수 {score}/100 · 현재가 ${trade['price']:.2f}\n" + "\n".join(f"• {reason}" for reason in reasons)
        db.commit()
        result = {"type": "signal", "symbol": ticker, "price": trade["price"], "score": score, "signal": signal, "confidence": confidence, "reasons": reasons, "risks": risks, "events": emitted, "updated_at": snapshot.updated_at.isoformat()}
    for event in emitted:
        try:
            await send_telegram(f"⚠️ {ticker} 실시간 시장 이벤트\n{event}\n현재가 ${trade['price']:.2f} · 신호 {signal} ({score}/100)")
        except Exception:
            logger.exception("Telegram event alert failed for %s", ticker)
    if alert:
        try:
            await send_telegram(alert)
        except Exception:
            logger.exception("Telegram live signal alert failed for %s", ticker)
    return result
