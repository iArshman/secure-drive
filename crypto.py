import base64
from cryptography.fernet import Fernet

# Global Cipher Variable (Starts empty)
cipher = None

def init_cipher(key: bytes):
    """Called by main.py to set the key from Database"""
    global cipher
    try:
        cipher = Fernet(key)
        # print("✅ Crypto Module Initialized Successfully") 
    except Exception as e:
        print(f"❌ CRITICAL: Failed to initialize Cipher: {e}")
        exit(1)

def _check_cipher():
    if cipher is None:
        raise RuntimeError("Encryption system not initialized! Check database connection.")

def encrypt_data(data: bytes) -> bytes:
    """Encrypts raw bytes (File Content)"""
    _check_cipher()
    return cipher.encrypt(data)

def decrypt_data(data: bytes) -> bytes:
    """Decrypts raw bytes (File Content)"""
    _check_cipher()
    try:
        return cipher.decrypt(data)
    except Exception:
        # Return original data if decryption fails (e.g. for old unencrypted files)
        return data

def encrypt_name(name: str) -> str:
    """Encrypts filename to url-safe string for Drive"""
    _check_cipher()
    if not name: return "Untitled"
    # We encode the name to bytes, encrypt it, then decode back to string
    return cipher.encrypt(name.encode()).decode()

def decrypt_name(encrypted_name: str) -> str:
    """Decrypts filename to show in Bot"""
    _check_cipher()
    try:
        return cipher.decrypt(encrypted_name.encode()).decode()
    except Exception:
        # If decryption fails (e.g. file is not encrypted), return the original name
        return encrypted_name
