"""
Configuration file for Cloudyte Drive Bot
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Bot Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")

# Google OAuth Configuration
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "YOUR_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "YOUR_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://your-domain.com/oauth_callback")

# Google Drive API Scopes
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.metadata.readonly"
]

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = "secure_drive_bot"

# Web Server Configuration
PORT = int(os.getenv("PORT", 3000))

# Pagination Settings
FILES_PER_PAGE = 10
ACCOUNTS_PER_PAGE = 10

# ================= LOCAL SERVER (NEW) =================
env_local = str(os.getenv("USE_LOCAL_SERVER", "False")).lower()
USE_LOCAL_SERVER = env_local in ("true", "1", "yes")
LOCAL_SERVER_URL = os.getenv("LOCAL_SERVER_URL", "http://localhost:8081")

# ================= LIMITS================
if USE_LOCAL_SERVER:
    MAX_DOWNLOAD_SIZE = 2000 * 1024 * 1024 
    MAX_UPLOAD_SIZE = 2000 * 1024 * 1024   
else:
    # Telegram Cloud limits: Upload 50MB, Download 20MB
    MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024  
    MAX_UPLOAD_SIZE = 50 * 1024 * 1024
