import logging
from cryptography.fernet import Fernet, InvalidToken
import config

logger = logging.getLogger("ORBBot")

# Initialize Fernet cipher with the server-side encryption key
_fernet = None

def _get_fernet() -> Fernet:
    """Lazily initializes and returns the Fernet cipher instance."""
    global _fernet
    if _fernet is None:
        key = config.ENCRYPTION_KEY
        if not key:
            # Auto-generate a key for development (logged as warning)
            key = Fernet.generate_key().decode()
            logger.warning(
                "ENCRYPTION_KEY not set in environment. Generated a temporary key. "
                "Set ENCRYPTION_KEY in .env for production use."
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt_value(plaintext: str) -> str:
    """
    Encrypts a plaintext string using Fernet symmetric encryption.
    
    Parameters:
        plaintext (str): The value to encrypt (e.g., an API key).
        
    Returns:
        str: The encrypted value as a URL-safe base64 string.
    """
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """
    Decrypts a Fernet-encrypted string back to plaintext.
    
    Parameters:
        ciphertext (str): The encrypted value.
        
    Returns:
        str: The decrypted plaintext string.
        
    Raises:
        ValueError: If decryption fails (wrong key or corrupted data).
    """
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Failed to decrypt value. The encryption key may have changed.")
