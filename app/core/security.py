from cryptography.fernet import Fernet, InvalidToken
from app.config import settings
from app.core.logging import logger # Import structured logger

# Initialize Fernet with the encryption key from settings
# The key must be 32 url-safe base64-encoded bytes.
# Fernet.generate_key() can be used to generate a new key.
# For production, ensure this key is securely managed and not hardcoded.

try:
    f = Fernet(settings.ENCRYPTION_KEY.encode('utf-8'))
except Exception as e:
    raise ValueError(f"Invalid ENCRYPTION_KEY. Ensure it is 32 url-safe base64-encoded bytes. Error: {e}")

def encrypt_data(data: str) -> str:
    """Encrypts a string using AES-256."""
    return f.encrypt(data.encode('utf-8')).decode('utf-8')

def decrypt_data(encrypted_data: str) -> str:
    """Decrypts an AES-256 encrypted string."""
    try:
        return f.decrypt(encrypted_data.encode('utf-8')).decode('utf-8')
    except InvalidToken:
        logger.error("Decryption failed: InvalidToken. Data might be corrupted or encrypted with a different key.", encrypted_data_prefix=encrypted_data[:50], service="security")
        raise
    except Exception as e:
        logger.error("Decryption failed with unexpected error.", error=str(e), encrypted_data_prefix=encrypted_data[:50], service="security")
        raise

