from cryptography.fernet import Fernet

cipher = None

def init_cipher(key: bytes):
    global cipher
    try:
        cipher = Fernet(key)
    except:
        exit(1)

def _check():
    if not cipher: raise RuntimeError("Cipher not initialized")

# --- Encryption-aware helpers ---
# Called with an explicit `enabled` flag so each user's setting
# is respected at the call site without any global state.

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
    except:
        return data

def encrypt_name(name: str, enabled: bool = True) -> str:
    if not enabled:
        return name if name else "Untitled"
    _check()
    if not name: return "Untitled"
    return cipher.encrypt(name.encode()).decode()

def decrypt_name(enc_name: str, enabled: bool = True) -> str:
    if not enabled:
        return enc_name
    _check()
    try:
        return cipher.decrypt(enc_name.encode()).decode()
    except:
        return enc_name
