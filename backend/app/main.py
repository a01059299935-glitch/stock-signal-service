import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import desc, select, text
from .config import settings
from .analysis import trade_levels
from .db import SessionLocal, engine
from .models import AppState, NewsEvent, SignalHistory, Snapshot, WatchItem
from .providers import fetch_candles, search_stocks, send_telegram
from .tasks import analyze_live_trade, refresh_ticker, refresh_watchlist
from .digest import run_catchup_digest
from .backup import create_backup_if_due, storage_status
from .auth import is_authorized
from .realtime import consume, tickers
from .market import MarketDataManager

trade_clients = MarketDataManager()
_digest_task_lock = asyncio.Lock()
_digest_task = None
_watchlist_task_lock = asyncio.Lock()
_watchlist_task = None


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


async def _local_refresh_loop():
    while True:
        try:
            result = await _refresh_watchlist_once()
            app.state.last_refresh_completed_at = datetime.now(timezone.utc).isoformat()
            app.state.last_refresh_result = result
        except Exception:
            logging.exception("Periodic watchlist refresh failed")
            app.state.last_refresh_result = {"error": "refresh failed"}
        await asyncio.sleep(max(30, settings.market_data_interval_seconds))


async def _refresh_watchlist_once():
    global _watchlist_task
    async with _watchlist_task_lock:
        if _watchlist_task is None or _watchlist_task.done():
            _watchlist_task = asyncio.create_task(refresh_watchlist())
        task = _watchlist_task
    try:
        return await task
    finally:
        async with _watchlist_task_lock:
            if _watchlist_task is task and task.done():
                _watchlist_task = None


@asynccontextmanager
async def lifespan(app):
    await trade_clients.set_watchlist(tickers())
    app.state.finnhub_task = asyncio.create_task(consume(trade_clients, _analyze_received_trade))
    app.state.refresh_task = asyncio.create_task(_local_refresh_loop())
    try:
        yield
    finally:
        for task in (app.state.finnhub_task, app.state.refresh_task):
            task.cancel()
        await asyncio.gather(app.state.finnhub_task, app.state.refresh_task, return_exceptions=True)


async def _analyze_received_trade(trade):
    if trade_clients.should_analyze(trade["symbol"]):
        try:
            update = await analyze_live_trade(trade, trade_clients)
            if update:
                await trade_clients.broadcast(update)
        except Exception:
            logging.exception("Live signal analysis failed for %s", trade["symbol"])


app = FastAPI(title="Stock Signal", description="투자 판단 보조 서비스. 자동 매매 기능은 제공하지 않습니다.", lifespan=lifespan)
app.state.last_refresh_completed_at = None
app.state.last_refresh_result = None
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])


@app.middleware("http")
async def basic_rate_limit(request: Request, call_next):
    if request.url.path != "/api/health":
        if settings.app_environment.casefold() == "production" and (not settings.app_username or not settings.app_password):
            return JSONResponse({"detail": "서비스 인증 설정이 필요합니다."}, status_code=503)
        if (settings.app_username or settings.app_password) and not is_authorized(request.headers.get("authorization")):
            return JSONResponse({"detail": "인증이 필요합니다."}, status_code=401, headers={"WWW-Authenticate": 'Basic realm="Stock Signal"'})
    # Basic per-process protection for write actions; use an API gateway for public multi-replica deployments.
    if request.method in {"POST", "DELETE"} and request.url.path.startswith("/api/"):
        ip = request.client.host if request.client else "unknown"
        now = datetime.now(timezone.utc).timestamp(); window = app.state.rate_window
        hits = [t for t in window.get(ip, []) if now - t < 60]
        if len(hits) >= 30: return __import__("fastapi").responses.JSONResponse({"detail": "요청이 너무 많습니다."}, status_code=429)
        window[ip] = hits + [now]
    return await call_next(request)

app.state.rate_window = {}


def snapshot_dict(s):
    ind = json.loads(s.indicators)
    return {"price": s.price, "change_pct": s.change_pct, "previous_close": s.previous_close, "volume": s.volume, "market_cap": s.market_cap, "score": s.score, "signal": s.signal, "confidence": s.confidence, "reasons": json.loads(s.reasons), "risks": json.loads(s.risks), "indicators": ind, "levels": trade_levels(s.price, ind), "updated_at": as_utc(s.updated_at)}


@app.get("/api/health")
def health():
    db_ok = False
    db_size_bytes = None
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")); db_ok = True
            try: db_size_bytes = connection.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
            except Exception: db_size_bytes = None
    except Exception: pass
    return {"status": "ok" if db_ok else "degraded", "database": "online" if db_ok else "offline", "database_size_bytes": db_size_bytes, "backup": storage_status(), "finnhub": bool(settings.finnhub_api_key), "opendart": bool(settings.opendart_api_key), "realtime": trade_clients.status if settings.finnhub_api_key else "Finnhub key required", "realtime_show_milliseconds": settings.realtime_show_milliseconds, "telegram": bool(settings.telegram_bot_token and settings.telegram_chat_id), "ai": "rule-based (OpenAI integration not enabled)", "refresh_interval_seconds": settings.market_data_interval_seconds, "last_refresh_completed_at": app.state.last_refresh_completed_at, "last_refresh_result": app.state.last_refresh_result}


@app.get("/api/watchlist")
def watchlist():
    with SessionLocal() as db:
        out = []
        for item in db.scalars(select(WatchItem).order_by(WatchItem.ticker)).all():
            s = db.scalar(select(Snapshot).where(Snapshot.ticker == item.ticker).order_by(desc(Snapshot.updated_at)).limit(1))
            out.append({"ticker": item.ticker, "snapshot": snapshot_dict(s) if s else None})
        return out


@app.get("/api/search")
async def search(q: str = Query(min_length=1, max_length=80)):
    return await search_stocks(q, limit=12)


def _digest_watermark():
    with SessionLocal() as db:
        state = db.get(AppState, 1)
        return state.last_successful_visit_at if state else None


def _digest_records():
    with SessionLocal() as db:
        return db.scalars(select(NewsEvent).order_by(desc(NewsEvent.published_at), desc(NewsEvent.created_at))).all()


def _save_digest_watermark(value):
    with SessionLocal() as db:
        state = db.get(AppState, 1)
        if state is None:
            state = AppState(id=1, last_successful_visit_at=value, updated_at=value)
            db.add(state)
        else:
            state.last_successful_visit_at = value
            state.updated_at = value
        db.commit()


async def _perform_digest_refresh():
    recent = app.state.last_refresh_completed_at
    last_result = app.state.last_refresh_result or {}
    recent_success = False
    if recent and not last_result.get("failed") and not last_result.get("error"):
        try:
            recent_success = (datetime.now(timezone.utc) - datetime.fromisoformat(recent)).total_seconds() < settings.market_data_interval_seconds
        except ValueError:
            recent_success = False
    refresh = (lambda: last_result) if recent_success else _refresh_watchlist_once
    result = await run_catchup_digest(refresh, _digest_records, _digest_watermark, _save_digest_watermark)
    if not result["stale"]:
        result["backup"] = await asyncio.to_thread(create_backup_if_due)
    return result


@app.post("/api/digest/refresh")
async def digest_refresh():
    global _digest_task
    async with _digest_task_lock:
        if _digest_task is None or _digest_task.done():
            _digest_task = asyncio.create_task(_perform_digest_refresh())
        task = _digest_task
    try:
        return await task
    finally:
        async with _digest_task_lock:
            if _digest_task is task and task.done():
                _digest_task = None


async def _refresh_one(ticker: str):
    try: await refresh_ticker(ticker)
    except Exception: logging.exception("Immediate refresh failed for %s", ticker)


@app.post("/api/watchlist/{ticker}")
async def add_ticker(ticker: str, background_tasks: BackgroundTasks):
    ticker = ticker.upper().strip()
    if not re.fullmatch(r"[A-Z0-9.^=-]{1,12}", ticker): raise HTTPException(400, "올바른 티커 형식이 아닙니다.")
    with SessionLocal() as db:
        if not db.scalar(select(WatchItem).where(WatchItem.ticker == ticker)): db.add(WatchItem(ticker=ticker)); db.commit()
    await trade_clients.set_watchlist(tickers())
    background_tasks.add_task(_refresh_one, ticker)
    return {"ticker": ticker, "queued": False, "refresh": "scheduled in process"}


@app.delete("/api/watchlist/{ticker}")
async def remove_ticker(ticker: str):
    with SessionLocal() as db:
        item = db.scalar(select(WatchItem).where(WatchItem.ticker == ticker.upper()))
        if item: db.delete(item); db.commit()
    await trade_clients.set_watchlist(tickers())
    return {"removed": ticker.upper()}


@app.get("/api/stocks/{ticker}")
def stock_detail(ticker: str):
    ticker = ticker.upper()
    with SessionLocal() as db:
        s = db.scalar(select(Snapshot).where(Snapshot.ticker == ticker).order_by(desc(Snapshot.updated_at)).limit(1))
        if not s: raise HTTPException(404, "종목 데이터가 아직 없습니다. API Key 확인 후 잠시 기다려 주세요.")
        news = db.scalars(select(NewsEvent).where(NewsEvent.ticker == ticker).order_by(desc(NewsEvent.source.like("SEC EDGAR%")), desc(NewsEvent.published_at), desc(NewsEvent.created_at)).limit(30)).all()
        history = db.scalars(select(SignalHistory).where(SignalHistory.ticker == ticker).order_by(desc(SignalHistory.created_at)).limit(30)).all()
        def history_item(h):
            try: reason = json.loads(h.reason)
            except (TypeError, json.JSONDecodeError): reason = {"evidence": [h.reason] if h.reason else []}
            return {"signal": h.signal, "score": h.score, "previous_signal": reason.get("previous_signal"), "previous_score": reason.get("previous_score"), "evidence": reason.get("evidence", []), "reason": h.reason, "created_at": as_utc(h.created_at)}
        return {"ticker": ticker, "snapshot": snapshot_dict(s), "news": [{"title": n.title, "source": n.source, "url": n.url, "summary": n.summary, "impact": n.impact, "sentiment": n.sentiment, "created_at": as_utc(n.created_at), "published_at": as_utc(n.published_at)} for n in news], "history": [history_item(h) for h in history]}


@app.get("/api/stocks/{ticker}/candles")
async def stock_candles(ticker: str, timeframe: str = Query(default="1m"), range: str = Query(default="RT")):
    try:
        return await fetch_candles(ticker, timeframe, range)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/telegram/test")
async def telegram_test():
    try: await send_telegram("✅ Stock Signal Telegram 연결 테스트가 완료되었습니다.")
    except Exception as e: raise HTTPException(400, str(e))
    return {"sent": True}


@app.websocket("/api/ws/trades")
async def stream_trades(websocket: WebSocket):
    if not is_authorized(websocket.headers.get("authorization")):
        await websocket.close(code=4401)
        return
    await trade_clients.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logging.debug("Frontend trade socket closed (%s)", type(exc).__name__)
    finally:
        trade_clients.disconnect(websocket)


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith("api/") and "." not in path:
                return await super().get_response("index.html", scope)
            raise


static_dir = __import__("pathlib").Path(__file__).resolve().parents[2] / "frontend" / "dist"
if static_dir.is_dir():
    app.mount("/", SPAStaticFiles(directory=static_dir, html=True), name="frontend")
