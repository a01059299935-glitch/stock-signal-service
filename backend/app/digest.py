import inspect
from datetime import datetime, timedelta, timezone


def _value(record, key):
    return record.get(key) if isinstance(record, dict) else getattr(record, key, None)


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_missed_digest(records, last_visit_at):
    watermark = _utc(last_visit_at)
    groups = {}
    seen = set()
    for record in records:
        published = _utc(_value(record, "published_at"))
        if published is None or (watermark is not None and published <= watermark):
            continue
        ticker = str(_value(record, "ticker") or "").upper()
        fingerprint = str(_value(record, "fingerprint") or "")
        identity = (ticker, fingerprint) if fingerprint else (ticker, _value(record, "title"), published)
        if identity in seen:
            continue
        seen.add(identity)
        impact = int(_value(record, "impact") or 0)
        group = groups.setdefault((ticker, impact), {"ticker": ticker, "impact": impact, "items": []})
        group["items"].append({
            "fingerprint": fingerprint,
            "title": _value(record, "title"),
            "source": _value(record, "source"),
            "summary": _value(record, "summary"),
            "url": _value(record, "url"),
            "published_at": published.isoformat(),
        })
    result = list(groups.values())
    for group in result:
        group["items"].sort(key=lambda item: item["published_at"], reverse=True)
    return sorted(result, key=lambda item: (-item["impact"], item["ticker"]))


async def _invoke(callback, *args):
    value = callback(*args)
    return await value if inspect.isawaitable(value) else value


async def run_catchup_digest(refresh, load_records, get_watermark, save_watermark, now=None):
    """Refresh once, build a source-backed digest, and only then move the watermark."""
    now = _utc(now or datetime.now(timezone.utc))
    previous = _utc(await _invoke(get_watermark))
    since = previous or now - timedelta(hours=24)
    try:
        result = await _invoke(refresh)
        if result and result.get("failed"):
            raise RuntimeError("one or more provider refreshes failed")
        items = build_missed_digest(await _invoke(load_records), since)
        await _invoke(save_watermark, now)
        return {"since": since.isoformat(), "synced_at": now.isoformat(), "items": items, "stale": False}
    except Exception:
        try:
            items = build_missed_digest(await _invoke(load_records), since)
        except Exception:
            items = []
        return {"since": since.isoformat(), "synced_at": previous.isoformat() if previous else None, "items": items, "stale": True}
