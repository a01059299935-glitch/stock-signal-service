from app.analysis import indicators, score_snapshot


def bars(count=260):
    return [{"time": i, "open": 10 + i / 10, "high": 11 + i / 10, "low": 9 + i / 10, "close": 10 + i / 10, "volume": 1000 + i} for i in range(count)]


def test_indicators_cover_daily_series():
    series = bars()
    result = indicators([x["close"] for x in series], [x["volume"] for x in series], series)
    assert result["ma200"] is not None
    assert result["macd"] is not None
    assert result["atr14"] is not None
    assert result["ema9"] is not None and result["ema20"] is not None and result["ema50"] is not None
    assert result["ma100"] is not None and result["ma200"] is not None
    assert result["macd_signal"] is not None and result["macd_histogram"] is not None
    assert result["volume_avg20"] > 0
    assert result["bollinger_upper"] > result["bollinger_lower"]
    assert result["high_52w"] > result["low_52w"]


def test_single_article_cannot_dominate_signal():
    series = bars()
    ind = indicators([x["close"] for x in series], [x["volume"] for x in series], series)
    score, signal, confidence, reasons, risks = score_snapshot(35, 0, [{"sentiment": 100}], ind)
    assert 0 <= score <= 100
    assert signal in {"STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"}
    assert confidence <= 90
    assert reasons and any("펀더멘털" in x for x in risks)


def test_score_stays_finite_without_data():
    score, signal, confidence, reasons, risks = score_snapshot(100, 0, [], {})
    assert score == 50 and signal == "HOLD"
    assert confidence >= 0 and reasons == ["당일 등락률 +0.00%"] and risks


def test_trade_levels_include_explained_buy_and_profit_zones():
    from app.analysis import trade_levels

    levels = trade_levels(100, {"low_20": 90, "high_20": 110, "atr14": 2, "ma20": 98, "ma50": 95})
    assert levels["buy_interest_zone"] and levels["additional_buy_zone"]
    assert levels["take_profit_zone"] and levels["invalidation"] < levels["buy_interest_zone"][0]
    assert levels["risk_reward"] > 0
    assert levels["explanations"]["buy_interest_zone"]


def test_indicators_use_only_available_prefix_data():
    series = bars()
    prefix = indicators([x["close"] for x in series[:220]], [x["volume"] for x in series[:220]], series[:220])
    full = indicators([x["close"] for x in series], [x["volume"] for x in series], series)
    assert prefix["ma20"] == round(sum(x["close"] for x in series[200:220]) / 20, 2)
    assert full["ma20"] == round(sum(x["close"] for x in series[-20:]) / 20, 2)
    assert prefix["ma200"] != full["ma200"]


def test_flat_prices_have_neutral_rsi_and_unavailable_long_averages():
    result = indicators([100.0] * 30, [1000] * 30)
    assert result["rsi14"] == 50
    assert result["ma20"] == 100
    assert result["ma50"] is None and result["ma200"] is None


def test_weekly_provider_candles_merge_a_partial_bar_into_the_same_week():
    from app.providers import _aggregate_bars

    weekly = _aggregate_bars([
        {"time": 1_790_001_000, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100},
        {"time": 1_790_050_000, "open": 11, "high": 13, "low": 10, "close": 12, "volume": 25},
    ], "1W")
    assert len(weekly) == 1
    assert weekly[0]["open"] == 10 and weekly[0]["high"] == 13
    assert weekly[0]["low"] == 9 and weekly[0]["close"] == 12
    assert weekly[0]["volume"] == 125
