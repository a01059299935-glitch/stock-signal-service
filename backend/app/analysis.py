import json
from datetime import datetime, timezone
from statistics import mean
from .config import settings


def indicators(closes: list[float], volumes: list[int], bars: list[dict] | None = None) -> dict:
    if not closes:
        return {}
    def sma(n):
        return round(mean(closes[-n:]), 2) if len(closes) >= n else None
    def ema_series(values, n):
        if len(values) < n:
            return []
        alpha = 2 / (n + 1)
        current = mean(values[:n])
        result = [None] * (n - 1) + [current]
        for value in values[n:]:
            current = value * alpha + current * (1 - alpha)
            result.append(current)
        return result
    changes = [closes[i] - closes[i - 1] for i in range(max(1, len(closes) - 14), len(closes))]
    gains = [max(x, 0) for x in changes]
    losses = [abs(min(x, 0)) for x in changes]
    avg_gain = mean(gains) if gains else 0
    avg_loss = mean(losses) if losses else 0
    rsi = 50 if not avg_gain and not avg_loss else 100 if not avg_loss else round(100 - 100 / (1 + avg_gain / avg_loss), 1)
    vavg = mean(volumes[-20:]) if volumes else 0
    ema9 = ema_series(closes, 9)
    ema20 = ema_series(closes, 20)
    ema50 = ema_series(closes, 50)
    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    macd_values = [fast - slow for fast, slow in zip(ema12, ema26) if fast is not None and slow is not None]
    signal_series = ema_series(macd_values, 9)
    macd_signal = signal_series[-1] if signal_series else None
    macd = macd_values[-1] if macd_values else None
    highs = [b["high"] for b in (bars or []) if "high" in b]
    lows = [b["low"] for b in (bars or []) if "low" in b]
    valid = [b for b in (bars or []) if all(k in b for k in ("high", "low", "close"))]
    tr = [max(b["high"] - b["low"], abs(b["high"] - valid[i - 1]["close"]) if i else b["high"] - b["low"], abs(b["low"] - valid[i - 1]["close"]) if i else b["high"] - b["low"]) for i, b in enumerate(valid)]
    atr = mean(tr[:14]) if len(tr) >= 14 else None
    if atr is not None:
        for value in tr[14:]:
            atr = (atr * 13 + value) / 14
    avg20 = mean(closes[-20:]) if len(closes) >= 20 else None
    sd20 = (sum((x - avg20) ** 2 for x in closes[-20:]) / 20) ** .5 if avg20 is not None else None
    return {"rsi14": rsi, "ma20": sma(20), "ma50": sma(50), "ma100": sma(100), "ma200": sma(200),
            "ema9": round(ema9[-1], 2) if ema9 else None,
            "ema20": round(ema20[-1], 2) if ema20 else None,
            "ema50": round(ema50[-1], 2) if ema50 else None,
            "macd": round(macd, 3) if macd is not None else None,
            "macd_signal": round(macd_signal, 3) if macd_signal is not None else None,
            "macd_histogram": round(macd - macd_signal, 3) if macd is not None and macd_signal is not None else None,
            "bollinger_upper": round(avg20 + 2 * sd20, 2) if sd20 is not None else None,
            "bollinger_lower": round(avg20 - 2 * sd20, 2) if sd20 is not None else None,
            "atr14": round(atr, 3) if atr is not None else None,
            "volume_avg20": round(vavg), "volume_ratio": round(volumes[-1] / vavg, 2) if volumes and vavg else 0,
            "high_20": round(max(highs[-20:]), 2) if highs else round(max(closes[-20:]), 2),
            "low_20": round(min(lows[-20:]), 2) if lows else round(min(closes[-20:]), 2),
            "high_52w": round(max(highs[-252:]), 2) if highs else None,
            "low_52w": round(min(lows[-252:]), 2) if lows else None}


def score_snapshot(price: float, change_pct: float, news: list[dict], ind: dict) -> tuple[int, str, int, list[str], list[str]]:
    # Transparent weighted rules use real provider values and identify any unavailable factor.
    now = datetime.now(timezone.utc); weighted_news = []
    for n in news[:20]:
        published = n.get("published_at")
        if isinstance(published, str): published = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if published and published.tzinfo is None: published = published.replace(tzinfo=timezone.utc)
        age_days = max(0, (now - published).total_seconds() / 86400) if published else 0
        decay = 1.0 if age_days < .25 else .8 if age_days < 1 else .6 if age_days < 3 else .3 if age_days < 7 else .1
        weight = max(.1, n.get("impact", 50) / 100) * decay
        weighted_news.append((n["sentiment"], weight))
    news_sentiment = sum(value * weight for value, weight in weighted_news) / sum(weight for _, weight in weighted_news) if weighted_news else 0
    news_score = max(0, min(100, 50 + news_sentiment / 2))
    trend_score = max(0, min(100, 50 + (15 if ind.get("ma20") and price > ind["ma20"] else -10) + (15 if ind.get("ma50") and price > ind["ma50"] else -10) + max(-15, min(15, change_pct * 2))))
    technical = max(0, min(100, 65 + (15 if 40 <= ind.get("rsi14", 50) <= 68 else -20 if ind.get("rsi14", 50) > 75 else 0) + (10 if ind.get("macd") is not None and ind.get("macd_signal") is not None and ind["macd"] > ind["macd_signal"] else -5)))
    volume = max(0, min(100, 50 + (ind.get("volume_ratio", 1) - 1) * 20))
    fundamentals = ind.get("fundamentals") or {}
    fundamental = fundamentals.get("score")
    market = (ind.get("market") or {}).get("score")
    event_risk = (ind.get("event_risk") or {}).get("score")
    event_available = event_risk is not None
    fundamental = 50 if fundamental is None else fundamental
    market = 50 if market is None else market
    event_risk = 50 if event_risk is None else event_risk
    weights = [settings.weight_news, settings.weight_trend, settings.weight_volume, settings.weight_technical, settings.weight_fundamental, settings.weight_market]
    components = [news_score, trend_score, volume, technical, fundamental, market]
    weight_total = sum(weights) or 1
    score = round(max(0, min(100, (sum(value * weight for value, weight in zip(components, weights)) / weight_total) * (0.9 + event_risk / 500))))
    signal = "STRONG BUY" if score >= 80 else "BUY" if score >= 65 else "HOLD" if score >= 40 else "SELL" if score >= 25 else "STRONG SELL"
    reasons = []
    if news: reasons.append(f"최근 뉴스 평균 감성 {news_sentiment:+.0f} (기사 {len(news)}건)")
    if ind.get("ma20"): reasons.append(f"현재가가 MA20 {'위' if price > ind['ma20'] else '아래'}에 위치")
    if ind.get("volume_ratio"): reasons.append(f"20일 평균 대비 거래량 {ind['volume_ratio']:.2f}배")
    if ind.get("rsi14") is not None: reasons.append(f"RSI(14) {ind['rsi14']}")
    if fundamentals.get("score") is not None:
        reasons.append(f"펀더멘털 점수 {fundamentals['score']}/100 (TTM 지표 기반)")
    if (ind.get("market") or {}).get("change_pct") is not None:
        reasons.append(f"시장 기준 SPY {ind['market']['change_pct']:+.2f}%")
    if (ind.get("event_risk") or {}).get("label"):
        reasons.append(f"이벤트 위험: {ind['event_risk']['label']}")
    reasons.append(f"당일 등락률 {change_pct:+.2f}%")
    risks = []
    if ind.get("rsi14", 50) > 70: risks.append("RSI 과매수 구간으로 단기 조정 가능성")
    if ind.get("volume_ratio", 1) < .7: risks.append("거래량이 20일 평균보다 낮아 가격 움직임 신뢰도 제한")
    if not news: risks.append("최근 수집된 뉴스가 없어 뉴스 기반 판단을 반영하지 못함")
    if fundamentals.get("score") is None: risks.append("펀더멘털 데이터가 없어 중립 가중치를 사용함")
    if (ind.get("market") or {}).get("score") is None: risks.append("시장 기준(SPY) 데이터가 없어 중립 가중치를 사용함")
    if event_available and event_risk < 50: risks.append(f"가까운 일정 또는 중요 공시 이벤트 위험: {(ind.get('event_risk') or {}).get('label', '확인 필요')}")
    if not event_available: risks.append("실적 일정·공시 이벤트 위험 데이터를 확인하지 못함")
    confidence = min(95, 25 + min(20, len(news)) + (15 if ind.get("ma20") else 0) + (10 if ind.get("volume_ratio") else 0) + (10 if fundamentals.get("score") is not None else 0) + (10 if (ind.get("market") or {}).get("score") is not None else 0) + (5 if event_available else 0))
    return score, signal, confidence, reasons[:8], risks


def trade_levels(price: float, ind: dict) -> dict:
    support = ind.get("low_20")
    resistance = ind.get("high_20")
    atr = float(ind.get("atr14") or 0)
    ma20 = ind.get("ma20")
    ma50 = ind.get("ma50")
    floor = float(support or max(0, price - 2 * atr))
    invalidation = round(max(0, floor - atr), 2) if atr else None

    def zone(anchor, lower, upper):
        if anchor is None or not atr:
            return None
        lo = max(floor, float(anchor) - lower * atr)
        hi = min(price, float(anchor) + upper * atr)
        return [round(lo, 2), round(hi, 2)] if lo < hi else None

    first = zone(ma20, 0.5, 0.25)
    second = zone(ma50 if ma50 else support, 1.0, 0.15)
    take_profit = None
    if resistance and resistance > price and atr:
        take_profit = [round(max(price, resistance - 0.25 * atr), 2), round(resistance + 0.5 * atr, 2)]
    entry = first[1] if first else None
    risk = entry - invalidation if entry is not None and invalidation is not None else None
    reward = max(0, take_profit[0] - entry) if take_profit and entry is not None else None
    risk_reward = round(reward / risk, 2) if reward is not None and risk and risk > 0 else None
    explanations = {
        "buy_interest_zone": "20일 이동평균 ± 0.5 ATR, 최근 20일 지지선 아래로 확장하지 않음" if first else "MA20/ATR 이력 부족 또는 현재가 아래 진입 구간 없음",
        "additional_buy_zone": "MA50 또는 최근 20일 지지선 ± ATR, 무효화선 위에서 계산" if second else "추가 구간 계산에 필요한 MA50/지지/ATR 부족",
        "take_profit_zone": "최근 20일 고점(저항) − 0.25 ATR부터 +0.5 ATR" if take_profit else "현재가 위에 확인된 20일 저항 구간이 없음",
        "invalidation": "최근 20일 최저가 − ATR(14)" if invalidation is not None else "ATR(14) 부족",
        "risk_reward": "익절 관심구간 하단까지 보상 ÷ 진입 상단부터 무효화까지 위험" if risk_reward is not None else "유효한 진입·익절·무효화 구간이 모두 있을 때만 계산",
    }
    return {"first_entry": first, "second_entry": second, "buy_interest_zone": first, "additional_buy_zone": second, "take_profit_zone": take_profit, "support": support, "resistance": resistance, "invalidation": invalidation, "risk_reward": risk_reward, "explanations": explanations}


def dump(value):
    return json.dumps(value, ensure_ascii=False)
