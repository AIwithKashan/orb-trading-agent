import logging
import asyncio
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pathlib import Path
from pydantic import BaseModel

import config
import auth
import firebase_db
import bot_manager
import gold_bot_manager
from gold_strategy import run_gold_bos_backtest

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("ORBBot")

# ─── App ────────────────────────────────────────────────────────────────────
app = FastAPI(title="ORB Screener")


# ─── Auth dependency ─────────────────────────────────────────────────────────
async def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split("Bearer ")[1]
    decoded = auth.verify_id_token(token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid session token")
    return decoded


# ─── Startup: auto-start screeners for onboarded users ──────────────────────
@app.on_event("startup")
async def startup_event():
    logger.info("Server starting up. Auto-starting screeners for onboarded users...")

    def _auto_start():
        import time as _time
        _time.sleep(2)
        db = auth.get_firestore_client()
        if not db:
            logger.warning("Firestore not available. Cannot auto-start screeners.")
            return
        try:
            count = 0
            for doc in db.collection("users").stream():
                user_data = doc.to_dict()
                if user_data.get("onboarded", False):
                    uid = doc.id
                    symbols = firebase_db.get_tracked_tickers(uid) or bot_manager.DEFAULT_US_STOCKS
                    bot_manager.start_bot(uid, symbols)
                    # Auto-start gold scanner as well
                    gold_bot_manager.start_gold_bot(uid)
                    count += 1
            logger.info(f"Auto-started {count} screener threads and {count} Gold threads on startup.")
        except Exception as e:
            logger.error(f"Error auto-starting screeners: {e}")

    import threading
    threading.Thread(target=_auto_start, daemon=True).start()


# ─── Static files ────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def get_dashboard():
    tpl = Path(__file__).resolve().parent / "templates" / "index.html"
    try:
        return HTMLResponse(content=tpl.read_text(encoding="utf-8"))
    except Exception as e:
        return HTMLResponse(content=f"<h3>Failed to load template: {e}</h3>", status_code=500)

@app.get("/static/favicon.png")
def get_favicon():
    p = Path(__file__).resolve().parent / "static" / "favicon.png"
    return FileResponse(p) if p.exists() else HTMLResponse(status_code=204)

@app.get("/static/logo.png")
def get_logo():
    p = Path(__file__).resolve().parent / "static" / "logo.png"
    return FileResponse(p, media_type="image/png") if p.exists() else HTMLResponse(status_code=204)

@app.get("/favicon.ico")
def get_favicon_ico():
    p = Path(__file__).resolve().parent / "static" / "favicon.png"
    return FileResponse(p, media_type="image/png") if p.exists() else HTMLResponse(status_code=204)


# ─── Health ──────────────────────────────────────────────────────────────────
@app.get("/health")
@app.get("/api/health")
def health_check():
    db_ok = False
    try:
        db_ok = auth.get_firestore_client() is not None
    except Exception:
        pass
    return {"status": "healthy" if db_ok else "unhealthy", "database": "connected" if db_ok else "disconnected"}


# ─── Public config ───────────────────────────────────────────────────────────
@app.get("/api/config")
def get_public_config():
    return {
        "apiKey": config.FIREBASE_WEB_API_KEY,
        "authDomain": config.FIREBASE_AUTH_DOMAIN,
        "projectId": config.FIREBASE_PROJECT_ID,
    }


# ─── SSE live logs ───────────────────────────────────────────────────────────
async def log_generator(uid: str):
    sent = set()
    bot = bot_manager.get_bot(uid)
    if bot:
        for log in list(bot.logs):
            yield f"data: {log}\n\n"
            sent.add(log)
    while True:
        bot = bot_manager.get_bot(uid)
        if bot:
            for log in list(bot.logs):
                if log not in sent:
                    yield f"data: {log}\n\n"
                    sent.add(log)
            if len(sent) > 200:
                sent = set(list(bot.logs)[-100:])
        else:
            msg = "[SYSTEM] Screener is stopped."
            if msg not in sent:
                yield f"data: {msg}\n\n"
                sent.add(msg)
        await asyncio.sleep(1)

@app.get("/api/stream-logs")
async def stream_logs(token: Optional[str] = Query(None)):
    if not token:
        raise HTTPException(status_code=401, detail="Token required")
    decoded = auth.verify_id_token(token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid token")
    return StreamingResponse(log_generator(decoded["uid"]), media_type="text/event-stream")


# ─── Login ───────────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
async def handle_login(payload: Dict[str, str]):
    id_token = payload.get("id_token", "")
    if not id_token:
        raise HTTPException(status_code=400, detail="Missing id_token")
    decoded = auth.verify_id_token(id_token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid token")

    uid = decoded["uid"]
    email = decoded.get("email", "")
    display_name = decoded.get("name", "")

    user_data = firebase_db.create_or_update_user(uid, email, display_name)

    # Auto-start screener for this user if not already running
    if not bot_manager.get_bot(uid):
        symbols = firebase_db.get_tracked_tickers(uid) or bot_manager.DEFAULT_US_STOCKS
        bot_manager.start_bot(uid, symbols)

    return {
        "status": "success",
        "user": {
            "uid": uid,
            "email": email,
            "display_name": user_data.get("display_name", display_name),
            "onboarded": user_data.get("onboarded", False),
            "experience": user_data.get("experience", ""),
            "risk_tolerance": user_data.get("risk_tolerance", ""),
            "profile_pic": user_data.get("profile_pic", ""),
        },
    }


# ─── Screener status ─────────────────────────────────────────────────────────
@app.get("/api/status")
async def get_status(user: Dict[str, Any] = Depends(get_current_user)):
    uid = user["uid"]
    bot = bot_manager.get_bot(uid)
    user_info = firebase_db.get_user(uid) or {}

    if bot:
        status = bot.get_status()
    else:
        status = {
            "screener_running": False,
            "symbols_count": 0,
            "prices": {},
            "logs": ["[SYSTEM] Screener is stopped."],
            "activity": "Screener stopped.",
            "orb_levels_calculated": False,
            "breakout_signals": [],
            "signals_today": 0,
        }

    status["display_name"] = user_info.get("display_name", user_info.get("email", "User"))
    status["onboarded"] = user_info.get("onboarded", False)
    status["experience"] = user_info.get("experience", "")
    status["risk_tolerance"] = user_info.get("risk_tolerance", "")
    status["profile_pic"] = user_info.get("profile_pic", "")
    return status


# ─── ORB levels ──────────────────────────────────────────────────────────────
@app.get("/api/levels")
async def get_levels(user: Dict[str, Any] = Depends(get_current_user)):
    uid = user["uid"]
    bot = bot_manager.get_bot(uid)
    if bot and bot.orb_levels_calculated:
        return bot.get_levels()
    return {}


# ─── User settings ───────────────────────────────────────────────────────────
@app.post("/api/settings")
async def update_settings(payload: Dict[str, Any], user: Dict[str, Any] = Depends(get_current_user)):
    uid = user["uid"]
    settings = {}
    for field in ("onboarded", "experience", "risk_tolerance", "profile_pic", "display_name"):
        if field in payload:
            settings[field] = payload[field]

    if settings:
        firebase_db.save_user_settings(uid, settings)

    # Ensure screener is running after settings update
    if not bot_manager.get_bot(uid):
        symbols = firebase_db.get_tracked_tickers(uid) or bot_manager.DEFAULT_US_STOCKS
        bot_manager.start_bot(uid, symbols)

    return {"status": "success"}


# ─── Tracked tickers endpoints ────────────────────────────────────────────────
@app.get("/api/tickers")
async def get_tickers(user: Dict[str, Any] = Depends(get_current_user)):
    """Returns the current user's custom watchlist (merged with defaults)."""
    uid = user["uid"]
    saved = firebase_db.get_tracked_tickers(uid)
    symbols = saved if saved else bot_manager.DEFAULT_US_STOCKS
    return {"tickers": symbols, "count": len(symbols), "is_custom": bool(saved)}


class TickersPayload(BaseModel):
    tickers: List[str]

@app.post("/api/tickers")
async def set_tickers(payload: TickersPayload, user: Dict[str, Any] = Depends(get_current_user)):
    """Saves a completely new watchlist for the user and hot-swaps the screener."""
    uid = user["uid"]
    clean = [t.strip().upper() for t in payload.tickers if t.strip()]
    if not clean:
        raise HTTPException(status_code=400, detail="Ticker list cannot be empty.")

    ok = firebase_db.save_tracked_tickers(uid, clean)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save tickers to database.")

    # Hot-swap live screener
    bot = bot_manager.get_bot(uid)
    if bot:
        bot.update_symbols(clean)
    else:
        bot_manager.start_bot(uid, clean)

    return {"status": "success", "tickers": clean, "count": len(clean)}


class AddTickerPayload(BaseModel):
    ticker: str

@app.post("/api/tickers/add")
async def add_ticker(payload: AddTickerPayload, user: Dict[str, Any] = Depends(get_current_user)):
    """Adds a single ticker to the user's watchlist."""
    uid = user["uid"]
    symbol = payload.ticker.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol.")

    current = firebase_db.get_tracked_tickers(uid) or list(bot_manager.DEFAULT_US_STOCKS)
    if symbol in current:
        return {"status": "already_exists", "tickers": current, "count": len(current)}

    current.append(symbol)
    firebase_db.save_tracked_tickers(uid, current)

    bot = bot_manager.get_bot(uid)
    if bot:
        bot.update_symbols(current)
    else:
        bot_manager.start_bot(uid, current)

    return {"status": "success", "tickers": current, "count": len(current)}


class RemoveTickerPayload(BaseModel):
    ticker: str

@app.post("/api/tickers/remove")
async def remove_ticker(payload: RemoveTickerPayload, user: Dict[str, Any] = Depends(get_current_user)):
    """Removes a single ticker from the user's watchlist."""
    uid = user["uid"]
    symbol = payload.ticker.strip().upper()

    current = firebase_db.get_tracked_tickers(uid) or list(bot_manager.DEFAULT_US_STOCKS)
    if symbol not in current:
        return {"status": "not_found", "tickers": current, "count": len(current)}

    current.remove(symbol)
    if not current:
        raise HTTPException(status_code=400, detail="Cannot remove the last ticker. Add another first.")

    firebase_db.save_tracked_tickers(uid, current)

    bot = bot_manager.get_bot(uid)
    if bot:
        bot.update_symbols(current)

    return {"status": "success", "tickers": current, "count": len(current)}


@app.post("/api/tickers/reset")
async def reset_tickers(user: Dict[str, Any] = Depends(get_current_user)):
    """Resets the user's watchlist back to the default 50 stocks."""
    uid = user["uid"]
    firebase_db.save_tracked_tickers(uid, bot_manager.DEFAULT_US_STOCKS)
    bot = bot_manager.get_bot(uid)
    if bot:
        bot.update_symbols(bot_manager.DEFAULT_US_STOCKS)
    else:
        bot_manager.start_bot(uid, bot_manager.DEFAULT_US_STOCKS)
    return {"status": "success", "tickers": bot_manager.DEFAULT_US_STOCKS, "count": len(bot_manager.DEFAULT_US_STOCKS)}


# ─── Screener start / stop ────────────────────────────────────────────────────
@app.post("/api/screener/start")
async def start_screener(user: Dict[str, Any] = Depends(get_current_user)):
    uid = user["uid"]
    symbols = firebase_db.get_tracked_tickers(uid) or bot_manager.DEFAULT_US_STOCKS
    bot_manager.start_bot(uid, symbols)
    return {"status": "started", "symbols_count": len(symbols)}

@app.post("/api/screener/stop")
async def stop_screener(user: Dict[str, Any] = Depends(get_current_user)):
    uid = user["uid"]
    bot_manager.stop_bot(uid)
    return {"status": "stopped"}


# ─── Account deletion ─────────────────────────────────────────────────────────
@app.post("/api/account/delete")
async def delete_account(user: Dict[str, Any] = Depends(get_current_user)):
    uid = user["uid"]
    bot_manager.stop_bot(uid)
    ok = firebase_db.delete_user_data(uid)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete account data.")
    return {"status": "success"}


# ─── Gold BOS Strategy Endpoints ──────────────────────────────────────────────

@app.get("/api/gold/status")
async def gold_status(user: Dict[str, Any] = Depends(get_current_user)):
    """Returns the Gold BOS scanner status and structure levels."""
    uid = user["uid"]
    bot = gold_bot_manager.get_gold_bot(uid)
    if bot:
        return bot.get_status()
    return {
        "market_bias": "NEUTRAL",
        "current_price": 0,
        "bos_detected": False,
        "swing_highs": [],
        "swing_lows": [],
        "signals_history": [],
        "scanner_running": False,
        "timeframe": "15m",
        "last_scan": None,
        "activity": "Scanner not running.",
    }

@app.post("/api/gold/start")
async def start_gold_scanner(user: Dict[str, Any] = Depends(get_current_user)):
    """Starts the Gold BOS scanner for the authenticated user."""
    uid = user["uid"]
    gold_bot_manager.start_gold_bot(uid)
    return {"status": "started"}

@app.post("/api/gold/stop")
async def stop_gold_scanner(user: Dict[str, Any] = Depends(get_current_user)):
    """Stops the Gold BOS scanner."""
    uid = user["uid"]
    gold_bot_manager.stop_gold_bot(uid)
    return {"status": "stopped"}

@app.post("/api/gold/scan")
async def trigger_gold_scan(user: Dict[str, Any] = Depends(get_current_user)):
    """Triggers an immediate Gold scan and returns results."""
    uid = user["uid"]
    bot = gold_bot_manager.get_gold_bot(uid)
    
    if not bot:
        # Auto-start if not running
        bot = gold_bot_manager.start_gold_bot(uid)
        import time
        time.sleep(2)  # Give it a moment to do first scan
        
    return bot.get_status()

class GoldParamsPayload(BaseModel):
    swing_lookback: int = 5
    rr_ratio: float = 2.0
    timeframe: str = "15m"

@app.post("/api/gold/params")
async def update_gold_params(payload: GoldParamsPayload, user: Dict[str, Any] = Depends(get_current_user)):
    """Updates Gold BOS strategy parameters."""
    uid = user["uid"]
    bot = gold_bot_manager.get_gold_bot(uid)
    if bot:
        bot.update_params(
            swing_lookback=payload.swing_lookback,
            rr_ratio=payload.rr_ratio,
            timeframe=payload.timeframe
        )
        return {"status": "updated"}
    return {"status": "scanner_not_running"}

class GoldBacktestPayload(BaseModel):
    start_date: str
    end_date: str
    risk_dollars: float = 10.0
    rr_ratio: float = 2.0
    swing_lookback: int = 5
    timeframe: str = "15m"

@app.post("/api/gold/backtest")
async def gold_backtest(payload: GoldBacktestPayload, user: Dict[str, Any] = Depends(get_current_user)):
    """Runs a backtest of the Gold BOS strategy."""
    return run_gold_bos_backtest(
        start_date_str=payload.start_date,
        end_date_str=payload.end_date,
        risk_dollars=payload.risk_dollars,
        rr_ratio=payload.rr_ratio,
        swing_lookback=payload.swing_lookback,
        interval=payload.timeframe,
    )

# ─── Backtest (kept for reference) ───────────────────────────────────────────
class BacktestRequest(BaseModel):
    tickers: List[str]
    start_date: str
    end_date: str
    risk_dollars: float = 10.0
    rr_ratio: float = 1.5

@app.post("/api/backtest")
async def api_backtest(req: BacktestRequest, user: Dict[str, Any] = Depends(get_current_user)):
    from strategy import run_orb_backtest
    return run_orb_backtest(
        tickers=req.tickers,
        start_date_str=req.start_date,
        end_date_str=req.end_date,
        risk_dollars=req.risk_dollars,
        rr_ratio=req.rr_ratio,
    )
