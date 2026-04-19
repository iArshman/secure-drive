from cryptography.fernet import Fernet, InvalidToken
import logging

logger = logging.getLogger(__name__)
cipher = None


def init_cipher(key: bytes):
    global cipher
    try:
        cipher = Fernet(key)
        logger.info("Cipher initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize cipher: {e}")
        raise RuntimeError(f"Cipher init failed: {e}")


def _check():
    if not cipher:
        raise RuntimeError("Cipher not initialized. Call init_cipher() first.")


def encrypt_data(data: bytes, enabled: bool = True) -> bytes:
    if not enabled:
        return data
    _check()
    return cipher.encrypt(data)


def decrypt_data(data: bytes, enabled: bool = True) -> bytes:
    if not enabled:
        return data
    _check()
    try:
        return cipher.decrypt(data)
    except (InvalidToken, Exception) as e:
        # Return raw data if decryption fails (e.g. file was not encrypted)
        logger.warning(f"Decryption failed, returning raw data: {e}")
        return data


def encrypt_name(name: str, enabled: bool = True) -> str:
    if not enabled:
        return name if name else "Untitled"
    _check()
    if not name:
        return "Untitled"
    try:
        return cipher.encrypt(name.encode()).decode()
    except Exception as e:
        logger.error(f"Name encryption failed: {e}")
        return name


def decrypt_name(enc_name: str, enabled: bool = True) -> str:
    if not enabled:
        return enc_name if enc_name else ""
    _check()
    if not enc_name:
        return ""
    try:
        return cipher.decrypt(enc_name.encode()).decode()
    except (InvalidToken, Exception):
        # Not encrypted or different key — return as-is
        return enc_name
