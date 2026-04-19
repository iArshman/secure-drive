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

# REDIRECT_URI - Auto-detect based on environment
# If running locally, use http://localhost:3000, otherwise use configured domain
if os.getenv("USE_LOCAL_SERVER", "False").lower() in ("true", "1", "yes"):
    REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:3000/oauth_callback")
else:
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
# Define if bot should use local Telegram API server
USE_LOCAL_SERVER = os.getenv("USE_LOCAL_SERVER", "False").lower() in ("true", "1", "yes")
# Default local server port is usually 8081
LOCAL_SERVER_URL = os.getenv("LOCAL_SERVER_URL", "http://localhost:8081")

# ================= LIMITS =================
# Note: 1 MB = 1024 * 1024 bytes

if USE_LOCAL_SERVER:
    # Local Server Limits: 2000 MB (2GB)
    MAX_DOWNLOAD_SIZE = 2000 * 1024 * 1024
    MAX_UPLOAD_SIZE = 2000 * 1024 * 1024
else:
    # Standard Bot API Limits
    MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024  # 50 MB Download
    MAX_UPLOAD_SIZE = 20 * 1024 * 1024    # 20 MB Upload
