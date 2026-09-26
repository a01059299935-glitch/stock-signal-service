from app.market import MarketDataManager


def test_trade_updates_in_memory_price_and_minute_candle():
    market = MarketDataManager()

    update = market.apply_trade({"s": "NVDA", "p": 225.74, "t": 1790343106842, "v": 100})

    assert update["symbol"] == "NVDA"
    assert update["price"] == 225.74
    assert update["volume"] == 100
    assert update["bar"]["close"] == 225.74
    assert market.latest["NVDA"]["price"] == 225.74


def test_trade_candles_keep_ohlcv_and_start_new_minute():
    market = MarketDataManager()
    first = market.apply_trade({"s": "NVDA", "p": 100, "t": 1_800_000_000_100, "v": 10})
    second = market.apply_trade({"s": "NVDA", "p": 102, "t": 1_800_000_030_100, "v": 7})
    next_minute = market.apply_trade({"s": "NVDA", "p": 99, "t": 1_800_000_060_100, "v": 3})

    assert first["bar"] == {"time": 1_800_000_000, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 10}
    assert second["bar"] == {"time": 1_800_000_000, "open": 100, "high": 102, "low": 100, "close": 102, "volume": 17}
    assert next_minute["bar"] == {"time": 1_800_000_060, "open": 99, "high": 99, "low": 99, "close": 99, "volume": 3}


def test_live_trade_aggregates_supported_timeframes():
    market = MarketDataManager()
    first = market.apply_trade({"s": "NVDA", "p": 100, "t": 1_800_000_000_100, "v": 10})
    second = market.apply_trade({"s": "NVDA", "p": 102, "t": 1_800_000_030_100, "v": 7})

    for frame in ("5m", "15m", "1h", "1D", "1W", "1M"):
        bar = second["bars"][frame]
        assert bar["open"] == 100 and bar["high"] == 102 and bar["low"] == 100
        assert bar["close"] == 102 and bar["volume"] == 17
    assert first["bars"]["1D"]["time"] == second["bars"]["1D"]["time"]


def test_repeat_market_event_is_suppressed_during_cooldown():
    market = MarketDataManager()
    assert market.should_emit("NVDA", "SUPPORT_BREAKDOWN") is True
    assert market.should_emit("NVDA", "SUPPORT_BREAKDOWN") is False
    assert market.should_emit("NVDA", "RESISTANCE_BREAKOUT") is True
