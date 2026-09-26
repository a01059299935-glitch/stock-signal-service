import asyncio
import json
import logging

import websockets
from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import WatchItem

logger = logging.getLogger("stock_signal.realtime")


def supports_finnhub_realtime(symbol: str) -> bool:
    normalized = str(symbol).upper()
    return not (normalized.endswith((".KS", ".KQ")) or normalized.isdigit() and len(normalized) == 6)


def tickers():
    with SessionLocal() as db:
        return {item.ticker for item in db.scalars(select(WatchItem)).all() if supports_finnhub_realtime(item.ticker)}


async def consume(market, on_trade=None):
    delay = 1
    while True:
        if not settings.finnhub_api_key:
            await market.set_status("DISCONNECTED")
            await asyncio.sleep(30)
            continue
        try:
            await market.set_status("CONNECTING")
            async with websockets.connect(
                f"wss://ws.finnhub.io?token={settings.finnhub_api_key}",
                ping_interval=25,
                ping_timeout=15,
                close_timeout=5,
                max_queue=512,
            ) as socket:
                logger.info("Finnhub WebSocket connected")
                delay = 1
                active = set()
                logged_trade_symbols = set()
                await market.set_status("CONNECTING")

                async def synchronize():
                    nonlocal active
                    while True:
                        try:
                            market.watchlist_changed.clear()
                            desired = tickers()
                            for symbol in active - desired:
                                await socket.send(json.dumps({"type": "unsubscribe", "symbol": symbol}))
                            for symbol in desired - active:
                                await socket.send(json.dumps({"type": "subscribe", "symbol": symbol}))
                                if symbol == "NVDA":
                                    logger.info("NVDA SUBSCRIBED")
                            active = desired
                            try:
                                await asyncio.wait_for(market.watchlist_changed.wait(), timeout=5)
                            except asyncio.TimeoutError:
                                pass
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.warning("Watchlist subscription sync failed (%s)", type(exc).__name__)
                            await asyncio.sleep(5)

                sync_task = asyncio.create_task(synchronize())
                try:
                    async for raw in socket:
                        try:
                            payload = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if payload.get("type") == "error":
                            error = str(payload.get("msg", "unknown"))
                            if settings.finnhub_api_key:
                                error = error.replace(settings.finnhub_api_key, "[REDACTED]")
                            logger.error("Finnhub WebSocket error: %s", error[:200])
                            raise RuntimeError("Finnhub WebSocket returned an error")
                        if payload.get("type") != "trade":
                            continue
                        for item in payload.get("data", []):
                            if str(item.get("s", "")).upper() not in active or item.get("p") is None:
                                continue
                            trade = market.apply_trade(item)
                            if trade["symbol"] not in logged_trade_symbols:
                                logger.info("REAL TRADE RECEIVED symbol=%s price=%s timestamp=%s volume=%s", trade["symbol"], trade["price"], trade["timestamp"], trade["volume"])
                                logged_trade_symbols.add(trade["symbol"])
                            await market.broadcast(trade)
                            if on_trade:
                                asyncio.create_task(on_trade(trade))
                finally:
                    sync_task.cancel()
                    await asyncio.gather(sync_task, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Finnhub WebSocket disconnected (%s); retry in %ss", type(exc).__name__, delay)
            await market.set_status("RECONNECTING")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
