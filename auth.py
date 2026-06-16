import logging
from typing import Optional, Dict, Any
import firebase_admin
from firebase_admin import credentials, auth, firestore
import config

logger = logging.getLogger("ORBBot")

# Firebase app singleton
_firebase_app = None
_firestore_client = None


def init_firebase() -> bool:
    """
    Initializes Firebase Admin SDK with service account credentials.
    Returns True if successful, False otherwise.
    """
    global _firebase_app, _firestore_client
    
    if _firebase_app is not None:
        return True
    
    sa_path = config.FIREBASE_SERVICE_ACCOUNT_PATH
    if not sa_path:
        logger.warning("FIREBASE_SERVICE_ACCOUNT_PATH not set. Firebase features disabled.")
        return False
    
    try:
        cred = credentials.Certificate(sa_path)
        _firebase_app = firebase_admin.initialize_app(cred)
        _firestore_client = firestore.client()
        logger.info("Firebase Admin SDK initialized successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize Firebase: {e}")
        return False


def get_firestore_client():
    """Returns the Firestore client, initializing Firebase if needed."""
    global _firestore_client
    if _firestore_client is None:
        init_firebase()
    return _firestore_client


def verify_id_token(id_token: str) -> Optional[Dict[str, Any]]:
    """
    Verifies a Firebase ID token and returns the decoded claims.
    
    Parameters:
        id_token (str): The Firebase ID token from the client.
        
    Returns:
        Optional[Dict]: The decoded token claims (uid, email, name, etc.) or None if invalid.
    """
    if _firebase_app is None:
        if not init_firebase():
            return None
    
    try:
        decoded = auth.verify_id_token(id_token)
        return decoded
    except auth.InvalidIdTokenError:
        logger.warning("Invalid Firebase ID token received.")
        return None
    except auth.ExpiredIdTokenError:
        logger.warning("Expired Firebase ID token received.")
        return None
    except Exception as e:
        logger.error(f"Error verifying Firebase token: {e}")
        return None
