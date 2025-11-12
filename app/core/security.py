from cryptography.fernet import Fernet
from app.config import settings

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
    return f.decrypt(encrypted_data.encode('utf-8')).decode('utf-8')

