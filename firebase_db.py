import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
import pytz

from auth import get_firestore_client
from encryption import encrypt_value, decrypt_value

logger = logging.getLogger("ORBBot")


# =====================================================================
# USER OPERATIONS
# =====================================================================

def get_user(uid: str) -> Optional[Dict[str, Any]]:
    """Retrieves a user document from Firestore."""
    db = get_firestore_client()
    if not db:
        return None
    try:
        doc = db.collection("users").document(uid).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        logger.error(f"Error fetching user {uid}: {e}")
        return None


def create_or_update_user(uid: str, email: str, display_name: str = "") -> Dict[str, Any]:
    """Creates or updates a user document on login."""
    db = get_firestore_client()
    if not db:
        return {"uid": uid, "email": email, "display_name": display_name}
    
    try:
        user_ref = db.collection("users").document(uid)
        doc = user_ref.get()
        
        now = datetime.now(pytz.utc).isoformat()
        
        if doc.exists:
            # Update last login
            user_ref.update({
                "last_login": now,
                "display_name": display_name or doc.to_dict().get("display_name", "")
            })
            return user_ref.get().to_dict()
        else:
            # Create new user
            user_data = {
                "uid": uid,
                "email": email,
                "display_name": display_name,
                "alpaca_api_key": "",
                "alpaca_secret_key": "",
                "trade_limit": 3,
                "bot_active": True,
                "dry_run": False,
                "onboarded": False,
                "experience": "",
                "risk_tolerance": "",
                "profile_pic": "",
                "stop_loss_pct": 0.0,
                "take_profit_pct": 0.0,
                "risk_dollars": 10.0,
                "created_at": now,
                "last_login": now
            }
            user_ref.set(user_data)
            logger.info(f"Created new user: {email}")
            return user_data
    except Exception as e:
        logger.error(f"Error creating/updating user {uid}: {e}")
        return {"uid": uid, "email": email, "display_name": display_name}


def save_user_settings(uid: str, settings: Dict[str, Any]) -> bool:
    """Saves user settings (Alpaca keys, trade limit, bot_active, onboarding info)."""
    db = get_firestore_client()
    if not db:
        return False
    
    try:
        user_ref = db.collection("users").document(uid)
        update_data = {}
        
        # Encrypt API keys if provided
        if "alpaca_api_key" in settings and settings["alpaca_api_key"]:
            update_data["alpaca_api_key"] = encrypt_value(settings["alpaca_api_key"])
        if "alpaca_secret_key" in settings and settings["alpaca_secret_key"]:
            update_data["alpaca_secret_key"] = encrypt_value(settings["alpaca_secret_key"])
        
        # Plain settings
        if "trade_limit" in settings:
            update_data["trade_limit"] = max(1, min(20, int(settings["trade_limit"])))
        if "bot_active" in settings:
            update_data["bot_active"] = bool(settings["bot_active"])
        if "dry_run" in settings:
            update_data["dry_run"] = bool(settings["dry_run"])
        if "onboarded" in settings:
            update_data["onboarded"] = bool(settings["onboarded"])
        if "experience" in settings:
            update_data["experience"] = str(settings["experience"])
        if "risk_tolerance" in settings:
            update_data["risk_tolerance"] = str(settings["risk_tolerance"])
        if "profile_pic" in settings:
            update_data["profile_pic"] = str(settings["profile_pic"])
        if "display_name" in settings:
            update_data["display_name"] = str(settings["display_name"])
        if "stop_loss_pct" in settings:
            update_data["stop_loss_pct"] = float(settings["stop_loss_pct"])
        if "take_profit_pct" in settings:
            update_data["take_profit_pct"] = float(settings["take_profit_pct"])
        if "risk_dollars" in settings:
            update_data["risk_dollars"] = float(settings["risk_dollars"])
        
        if update_data:
            user_ref.update(update_data)
            logger.info(f"Settings saved for user {uid}")
        return True
    except Exception as e:
        logger.error(f"Error saving settings for user {uid}: {e}")
        return False


def delete_user_data(uid: str) -> bool:
    """Deletes all user settings data and their trade logs from Firestore."""
    db = get_firestore_client()
    if not db:
        return False
    
    try:
        # Delete trades subcollection
        trades_ref = db.collection("users").document(uid).collection("trades")
        docs = trades_ref.stream()
        for doc in docs:
            doc.reference.delete()
            
        # Delete user settings doc
        db.collection("users").document(uid).delete()
        logger.info(f"Deleted all records for user: {uid}")
        return True
    except Exception as e:
        logger.error(f"Error deleting user database records for {uid}: {e}")
        return False



def get_user_alpaca_keys(uid: str) -> Optional[Dict[str, str]]:
    """Retrieves and decrypts the user's Alpaca API keys."""
    user = get_user(uid)
    if not user:
        return None
    
    api_key = user.get("alpaca_api_key", "")
    secret_key = user.get("alpaca_secret_key", "")
    
    if not api_key or not secret_key:
        return None
    
    try:
        return {
            "api_key": decrypt_value(api_key),
            "secret_key": decrypt_value(secret_key)
        }
    except ValueError:
        logger.error(f"Failed to decrypt API keys for user {uid}")
        return None


# =====================================================================
# TRADE HISTORY OPERATIONS
# =====================================================================

def log_trade(uid: str, trade_data: Dict[str, Any]) -> Optional[str]:
    """
    Logs a trade to the user's trade history subcollection.
    Returns the trade document ID or None on failure.
    """
    db = get_firestore_client()
    if not db:
        return None
    
    try:
        trade_data["timestamp"] = datetime.now(pytz.utc).isoformat()
        trade_ref = db.collection("users").document(uid).collection("trades").document()
        trade_ref.set(trade_data)
        logger.info(f"Trade logged for user {uid}: {trade_data.get('symbol', '?')} {trade_data.get('side', '?')}")
        return trade_ref.id
    except Exception as e:
        logger.error(f"Error logging trade for user {uid}: {e}")
        return None


def update_trade(uid: str, trade_id: str, updates: Dict[str, Any]) -> bool:
    """Updates a trade record (e.g., when closed with realized P&L)."""
    db = get_firestore_client()
    if not db:
        return False
    
    try:
        trade_ref = db.collection("users").document(uid).collection("trades").document(trade_id)
        trade_ref.update(updates)
        return True
    except Exception as e:
        logger.error(f"Error updating trade {trade_id} for user {uid}: {e}")
        return False


def get_trade_history(uid: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Retrieves the user's trade history, most recent first."""
    db = get_firestore_client()
    if not db:
        return []
    
    try:
        trades_ref = (
            db.collection("users").document(uid).collection("trades")
            .order_by("timestamp", direction="DESCENDING")
            .limit(limit)
        )
        docs = trades_ref.stream()
        trades = []
        for doc in docs:
            trade = doc.to_dict()
            trade["id"] = doc.id
            trades.append(trade)
        return trades
    except Exception as e:
        logger.error(f"Error fetching trade history for user {uid}: {e}")
        return []


def get_trade_stats(uid: str) -> Dict[str, Any]:
    """Calculates cumulative P&L statistics for the user."""
    trades = get_trade_history(uid, limit=1000)
    
    if not trades:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "total_profit": 0.0,
            "total_loss": 0.0
        }
    
    closed_trades = [t for t in trades if t.get("pnl") is not None]
    
    if not closed_trades:
        return {
            "total_trades": len(trades),
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "total_profit": 0.0,
            "total_loss": 0.0
        }
    
    pnls = [t["pnl"] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round((len(wins) / len(closed_trades)) * 100, 1) if closed_trades else 0.0,
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "best_trade": round(max(pnls), 2) if pnls else 0.0,
        "worst_trade": round(min(pnls), 2) if pnls else 0.0,
        "total_profit": round(sum(wins), 2) if wins else 0.0,
        "total_loss": round(sum(losses), 2) if losses else 0.0
    }


def get_today_trades(uid: str) -> List[Dict[str, Any]]:
    """Gets trades from today (UTC) for daily limit checking."""
    db = get_firestore_client()
    if not db:
        return []
    
    try:
        today_start = datetime.now(pytz.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        trades_ref = (
            db.collection("users").document(uid).collection("trades")
            .where("timestamp", ">=", today_start)
            .order_by("timestamp", direction="DESCENDING")
        )
        docs = trades_ref.stream()
        return [{"id": doc.id, **doc.to_dict()} for doc in docs]
    except Exception as e:
        logger.error(f"Error fetching today's trades for user {uid}: {e}")
        return []


def get_open_trades(uid: str) -> List[Dict[str, Any]]:
    """Gets all open trades (where pnl is None) across any date."""
    db = get_firestore_client()
    if not db:
        return []
    
    try:
        trades_ref = (
            db.collection("users").document(uid).collection("trades")
            .where("pnl", "==", None)
        )
        docs = trades_ref.stream()
        return [{"id": doc.id, **doc.to_dict()} for doc in docs]
    except Exception as e:
        logger.error(f"Error fetching open trades for user {uid}: {e}")
        # Fallback: fetch last 100 trades and filter in memory
        try:
            history = get_trade_history(uid, limit=100)
            return [t for t in history if t.get("pnl") is None]
        except Exception as ex:
            logger.error(f"Fallback fetch open trades failed: {ex}")
            return []

