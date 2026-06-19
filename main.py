import logging
import asyncio
from typing import Dict, Any, Optional
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pathlib import Path

import config
import auth
import firebase_db
import bot_manager

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("ORBBot")
logger.setLevel(logging.INFO)

# FastAPI app
app = FastAPI(title="ORB Quantum Control Deck")

# Dependency: Get authenticated user from Firebase token
async def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    token = authorization.split("Bearer ")[1]
    decoded_token = auth.verify_id_token(token)
    if not decoded_token:
        raise HTTPException(status_code=401, detail="Invalid session token")
    
    return decoded_token

# Startup: Start bots for users who are onboarded to monitor open trades and handle sessions
@app.on_event("startup")
async def startup_event():
    logger.info("Server starting up. Initializing Firebase DB connection...")
    db = auth.get_firestore_client()
    if not db:
        logger.warning("Firestore client not available. Cannot auto-start user bots.")
        return
        
    try:
        users = db.collection("users").stream()
        active_count = 0
        for doc in users:
            user_data = doc.to_dict()
            uid = doc.id
            if user_data.get("onboarded", False):
                dry_run = user_data.get("dry_run", True)
                keys = firebase_db.get_user_alpaca_keys(uid)
                if dry_run or keys:
                    settings = {
                        "bot_active": user_data.get("bot_active", True),
                        "trade_limit": user_data.get("trade_limit", 3),
                        "dry_run": dry_run,
                        "stop_loss_pct": user_data.get("stop_loss_pct", 0.0),
                        "take_profit_pct": user_data.get("take_profit_pct", 0.0),
                        "risk_dollars": user_data.get("risk_dollars", 10.0)
                    }
                    bot_manager.start_bot(uid, keys, settings)
                    active_count += 1
        logger.info(f"Auto-started {active_count} user bot threads on startup.")
    except Exception as e:
        logger.error(f"Error auto-starting user bots on startup: {e}")

@app.get("/", response_class=HTMLResponse)
def get_dashboard() -> HTMLResponse:
    """Serves the 3D animated Stark HUD styled web dashboard."""
    template_path = Path(__file__).resolve().parent / "templates" / "index.html"
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        return HTMLResponse(content=f"<h3>Failed to load template: {e}</h3>", status_code=500)

@app.get("/static/favicon.png")
def get_favicon():
    """Serves the dashboard favicon."""
    favicon_path = Path(__file__).resolve().parent / "static" / "favicon.png"
    if favicon_path.exists():
        return FileResponse(favicon_path)
    return HTMLResponse(status_code=204)


@app.get("/static/logo.png")
def get_logo():
    """Serves the dashboard logo."""
    logo_path = Path(__file__).resolve().parent / "static" / "logo.png"
    if logo_path.exists():
        return FileResponse(logo_path, media_type="image/png")
    return HTMLResponse(status_code=204)


@app.get("/favicon.ico")
def get_favicon_ico():
    """Serves the favicon.ico from static/favicon.png for browser defaults."""
    favicon_path = Path(__file__).resolve().parent / "static" / "favicon.png"
    if favicon_path.exists():
        return FileResponse(favicon_path, media_type="image/png")
    return HTMLResponse(status_code=204)


@app.get("/health")
@app.get("/api/health")
def health_check():
    """System health check endpoint."""
    db_ok = False
    try:
        db = auth.get_firestore_client()
        if db is not None:
            db_ok = True
    except Exception:
        pass
        
    return {
        "status": "healthy" if db_ok else "unhealthy",
        "database": "connected" if db_ok else "disconnected",
        "uptime": "operational"
    }


async def log_generator(uid: str):
    # Keep track of logs we've sent to this specific client connection
    sent_logs = set()
    
    # Send all historical logs first
    bot = bot_manager.get_bot(uid)
    if bot:
        for log in list(bot.logs):
            yield f"data: {log}\n\n"
            sent_logs.add(log)
            
    while True:
        bot = bot_manager.get_bot(uid)
        if bot:
            current_logs = list(bot.logs)
            for log in current_logs:
                if log not in sent_logs:
                    yield f"data: {log}\n\n"
                    sent_logs.add(log)
            # Prevent set from growing infinitely
            if len(sent_logs) > 100:
                sent_logs = set(current_logs[-50:])
        else:
            standby_log = "[SYSTEM] Bot is standby / stopped."
            if standby_log not in sent_logs:
                yield f"data: {standby_log}\n\n"
                sent_logs.add(standby_log)
                
        await asyncio.sleep(1)


@app.get("/api/stream-logs")
async def stream_logs(token: Optional[str] = Query(None)):
    """SSE endpoint streaming live console logs for the active user bot."""
    if not token:
        raise HTTPException(status_code=401, detail="Authentication token is required")
        
    decoded = auth.verify_id_token(token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid token")
        
    uid = decoded["uid"]
    return StreamingResponse(log_generator(uid), media_type="text/event-stream")


@app.get("/api/config")
def get_public_config() -> Dict[str, Any]:
    """Exposes public Firebase configuration for client-side SDK initialization."""
    return {
        "apiKey": config.FIREBASE_WEB_API_KEY,
        "authDomain": config.FIREBASE_AUTH_DOMAIN,
        "projectId": config.FIREBASE_PROJECT_ID
    }

@app.post("/api/auth/login")
async def handle_login(payload: Dict[str, str]) -> Dict[str, Any]:
    """Handles Google Sign-In verification and user creation/updates."""
    id_token = payload.get("id_token", "")
    if not id_token:
        raise HTTPException(status_code=400, detail="Missing id_token")
        
    decoded = auth.verify_id_token(id_token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid token")
        
    uid = decoded["uid"]
    email = decoded.get("email", "")
    display_name = decoded.get("name", "")
    
    # Create or update user in Firestore
    user_data = firebase_db.create_or_update_user(uid, email, display_name)
    
    # Auto-start bot thread if active
    if user_data.get("bot_active", True):
        bot = bot_manager.get_bot(uid)
        if not bot:
            keys = firebase_db.get_user_alpaca_keys(uid)
            if keys:
                settings = {
                    "trade_limit": user_data.get("trade_limit", 3),
                    "dry_run": user_data.get("dry_run", False),
                    "stop_loss_pct": user_data.get("stop_loss_pct", 0.0),
                    "take_profit_pct": user_data.get("take_profit_pct", 0.0),
                    "risk_dollars": user_data.get("risk_dollars", 10.0)
                }
                bot_manager.start_bot(uid, keys, settings)
                
    # Return user details without secrets
    return {
        "status": "success",
        "user": {
            "uid": uid,
            "email": email,
            "display_name": user_data.get("display_name", display_name),
            "bot_active": user_data.get("bot_active", True),
            "trade_limit": user_data.get("trade_limit", 3),
            "dry_run": user_data.get("dry_run", False),
            "onboarded": user_data.get("onboarded", False),
            "experience": user_data.get("experience", ""),
            "risk_tolerance": user_data.get("risk_tolerance", ""),
            "profile_pic": user_data.get("profile_pic", ""),
            "stop_loss_pct": user_data.get("stop_loss_pct", 0.0),
            "take_profit_pct": user_data.get("take_profit_pct", 0.0),
            "risk_dollars": user_data.get("risk_dollars", 10.0),
            "has_keys": bool(user_data.get("alpaca_api_key"))
        }
    }

@app.get("/api/status")
async def get_status(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Exposes real-time state metrics of the current user's trading bot."""
    uid = user["uid"]
    bot = bot_manager.get_bot(uid)
    
    user_info = firebase_db.get_user(uid) or {}
    
    if bot:
        status = bot.get_status()
    else:
        # Bot not running, retrieve configuration from database
        status = {
            "bot_running": False,
            "dry_run": user_info.get("dry_run", False),
            "trade_count": 0,
            "max_trades": user_info.get("trade_limit", 3),
            "prices": {},
            "trades": [],
            "logs": ["[SYSTEM] Bot is standby / stopped."],
            "activity": "Bot is paused.",
            "account_equity": 0.0
        }
        
    # Inject Firestore user details into status response
    status["onboarded"] = user_info.get("onboarded", False)
    status["experience"] = user_info.get("experience", "")
    status["risk_tolerance"] = user_info.get("risk_tolerance", "")
    status["profile_pic"] = user_info.get("profile_pic", "")
    status["stop_loss_pct"] = user_info.get("stop_loss_pct", 0.0)
    status["take_profit_pct"] = user_info.get("take_profit_pct", 0.0)
    status["risk_dollars"] = user_info.get("risk_dollars", 10.0)
    status["display_name"] = user_info.get("display_name", user_info.get("email", "User"))
    status["has_keys"] = bool(user_info.get("alpaca_api_key"))
    
    return status

@app.get("/api/levels")
async def get_levels(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Dict[str, float]]:
    """Exposes the ORB boundaries calculated for the user's active stocks."""
    uid = user["uid"]
    bot = bot_manager.get_bot(uid)
    if bot and bot.orb_levels_calculated:
        return bot.get_levels()
    return {}

@app.post("/api/settings")
async def update_settings(payload: Dict[str, Any], user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Updates the user's API settings and trade parameters."""
    uid = user["uid"]
    
    # Read settings fields
    settings = {}
    if "alpaca_api_key" in payload:
        settings["alpaca_api_key"] = payload["alpaca_api_key"]
    if "alpaca_secret_key" in payload:
        settings["alpaca_secret_key"] = payload["alpaca_secret_key"]
    if "trade_limit" in payload:
        settings["trade_limit"] = int(payload["trade_limit"])
    if "bot_active" in payload:
        settings["bot_active"] = bool(payload["bot_active"])
    if "dry_run" in payload:
        settings["dry_run"] = bool(payload["dry_run"])
    if "onboarded" in payload:
        settings["onboarded"] = bool(payload["onboarded"])
    if "experience" in payload:
        settings["experience"] = str(payload["experience"])
    if "risk_tolerance" in payload:
        settings["risk_tolerance"] = str(payload["risk_tolerance"])
    if "profile_pic" in payload:
        settings["profile_pic"] = str(payload["profile_pic"])
    if "display_name" in payload:
        settings["display_name"] = str(payload["display_name"])
    if "stop_loss_pct" in payload:
        settings["stop_loss_pct"] = float(payload["stop_loss_pct"])
    if "take_profit_pct" in payload:
        settings["take_profit_pct"] = float(payload["take_profit_pct"])
    if "risk_dollars" in payload:
        settings["risk_dollars"] = float(payload["risk_dollars"])
        
    success = firebase_db.save_user_settings(uid, settings)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save settings to database")
        
    # Apply thread management dynamically
    user_info = firebase_db.get_user(uid) or {}
    bot_active = user_info.get("bot_active", True)
    dry_run = user_info.get("dry_run", True)
    onboarded = user_info.get("onboarded", False)
    
    if onboarded:
        keys = firebase_db.get_user_alpaca_keys(uid)
        if not dry_run and not keys:
            # Force bot inactive in database, stop thread, and raise error
            firebase_db.save_user_settings(uid, {"bot_active": False})
            bot_manager.stop_bot(uid)
            raise HTTPException(status_code=400, detail="Cannot activate live trading without valid Alpaca API keys.")
        
        # Start/restart bot thread with updated settings, keeping it running to monitor open positions even if bot_active is False
        bot_manager.start_bot(uid, keys, {
            "bot_active": bot_active,
            "trade_limit": user_info.get("trade_limit", 3),
            "dry_run": dry_run,
            "stop_loss_pct": user_info.get("stop_loss_pct", 0.0),
            "take_profit_pct": user_info.get("take_profit_pct", 0.0),
            "risk_dollars": user_info.get("risk_dollars", 10.0)
        })
    else:
        # User not onboarded yet, keep bot thread stopped
        bot_manager.stop_bot(uid)
        
    return {"status": "success", "bot_active": bot_active}

@app.post("/api/toggle")
async def toggle_bot(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Toggles the bot running status."""
    uid = user["uid"]
    user_info = firebase_db.get_user(uid) or {}
    new_state = not user_info.get("bot_active", True)
    
    return await update_settings({"bot_active": new_state}, user)

@app.post("/api/toggle-dryrun")
async def toggle_dryrun(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Toggles dry run mode status."""
    uid = user["uid"]
    user_info = firebase_db.get_user(uid) or {}
    new_state = not user_info.get("dry_run", False)
    
    await update_settings({"dry_run": new_state}, user)
    return {"status": "success", "dry_run": new_state}

@app.post("/api/liquidate")
async def force_liquidate(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Triggers immediate EOD-style liquidation of all positions and orders for the current user."""
    uid = user["uid"]
    bot = bot_manager.get_bot(uid)
    if not bot:
        raise HTTPException(status_code=400, detail="Trading bot is not currently running.")
        
    bot.add_log("[MANUAL] Manual force liquidation request received.")
    bot.activity = "Force liquidating all positions..."
    
    success = True
    if not bot.dry_run and bot.broker:
        success = bot.broker.cancel_all_orders_and_close_positions()
    else:
        bot.add_log("[DRY-RUN] Simulating manual liquidation override.")
        
    if success:
        bot.add_log("[MANUAL] Liquidation execution successfully dispatched.")
        bot.active_trades.clear()  # Clear local list
    else:
        bot.add_log("[ERROR] Liquidation request returned broker faults.")
        
    return {"status": "success" if success else "failed"}

@app.get("/api/history")
async def get_history(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Retrieves the current user's persistent trade history and stats from Firestore."""
    uid = user["uid"]
    trades = firebase_db.get_trade_history(uid)
    stats = firebase_db.get_trade_stats(uid)
    return {
        "trades": trades,
        "stats": stats
    }

@app.post("/api/account/delete")
async def delete_account(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Deletes the current user's account settings and history, and halts their bot."""
    uid = user["uid"]
    bot_manager.stop_bot(uid)
    success = firebase_db.delete_user_data(uid)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete account data from Firestore.")
    return {"status": "success"}

