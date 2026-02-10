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

def encrypt_data(data: bytes) -> bytes:
    _check()
    return cipher.encrypt(data)

def decrypt_data(data: bytes) -> bytes:
    _check()
    try:
        return cipher.decrypt(data)
    except:
        return data

def encrypt_name(name: str) -> str:
    """Encrypts entire name. Google sees garbage."""
    _check()
    if not name: return "Untitled"
    return cipher.encrypt(name.encode()).decode()

def decrypt_name(enc_name: str) -> str:
    """Decrypts garbage back to real name."""
    _check()
    try:
        return cipher.decrypt(enc_name.encode()).decode()
    except:
        return enc_name
