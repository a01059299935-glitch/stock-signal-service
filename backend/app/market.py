import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
class MarketDataManager:
    """Single-process market state and FastAPI WebSocket fan-out."""

    def __init__(self):
        self.clients = set()
        self.latest = {}
        self.bars = {}
        self.interval_bars = {}
        self.watchlist = set()
        self.status = "CONNECTING"
        self.last_trade_at = None
        self.watchlist_changed = asyncio.Event()
        self._last_analysis = {}
        self._last_event = {}

    async def set_watchlist(self, symbols):
        self.watchlist = {str(symbol).upper() for symbol in symbols}
        self.watchlist_changed.set()

    def apply_trade(self, item):
        symbol = str(item["s"]).upper()
        price = float(item["p"])
        timestamp = int(item["t"])
        volume = float(item.get("v", 0))
        previous_trade = self.latest.get(symbol)
        previous_bars = {frame: bar.copy() for frame, bar in self.interval_bars.get(symbol, {}).items()}
        bars = self.interval_bars.setdefault(symbol, {})
        widths = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
        updated = {}
        for frame, seconds in widths.items():
            stamp = timestamp // (seconds * 1000) * seconds
            updated[frame] = self._update_bar(bars.get(frame), stamp, price, volume)
            bars[frame] = updated[frame]
        local = datetime.fromtimestamp(timestamp / 1000, ZoneInfo("America/New_York"))
        day_open = int(local.replace(hour=9, minute=30, second=0, microsecond=0).timestamp())
        updated["1D"] = self._update_bar(bars.get("1D"), day_open, price, volume)
        bars["1D"] = updated["1D"]
        week_start = local.date() - timedelta(days=local.weekday())
        week_stamp = int(datetime(week_start.year, week_start.month, week_start.day, tzinfo=local.tzinfo).timestamp())
        updated["1W"] = self._update_bar(bars.get("1W"), week_stamp, price, volume)
        bars["1W"] = updated["1W"]
        month_stamp = int(datetime(local.year, local.month, 1, tzinfo=local.tzinfo).timestamp())
        updated["1M"] = self._update_bar(bars.get("1M"), month_stamp, price, volume)
        bars["1M"] = updated["1M"]
        self.bars[symbol] = updated["1m"]
        trade = {"type": "trade", "symbol": symbol, "price": price, "timestamp": timestamp, "volume": volume, "bar": updated["1m"], "bars": updated, "previous_bars": previous_bars, "previous_price": previous_trade["price"] if previous_trade else None}
        self.latest[symbol] = trade
        return trade

    @staticmethod
    def _update_bar(previous, stamp, price, volume):
        if not previous or previous["time"] != stamp:
            return {"time": stamp, "open": price, "high": price, "low": price, "close": price, "volume": volume}
        return {"time": stamp, "open": previous["open"], "high": max(previous["high"], price), "low": min(previous["low"], price), "close": price, "volume": previous["volume"] + volume}

    def should_analyze(self, symbol, cooldown=1.0):
        now = time.monotonic()
        if now - self._last_analysis.get(symbol, 0) < cooldown:
            return False
        self._last_analysis[symbol] = now
        return True

    def should_emit(self, symbol, event, cooldown=300):
        now = time.monotonic()
        key = (symbol, event)
        if now - self._last_event.get(key, 0) < cooldown:
            return False
        self._last_event[key] = now
        return True

    async def broadcast(self, message):
        if message.get("type") == "trade":
            self.status = "LIVE"
            self.last_trade_at = time.monotonic()
        failed = []
        for client in tuple(self.clients):
            try:
                await client.send_json(message)
            except Exception:
                failed.append(client)
        for client in failed:
            self.clients.discard(client)

    async def set_status(self, status):
        self.status = status
        await self.broadcast({"type": "status", "status": status})

    async def connect(self, websocket):
        await websocket.accept()
        self.clients.add(websocket)
        await websocket.send_json({"type": "status", "status": self.status})

    def disconnect(self, websocket):
        self.clients.discard(websocket)
