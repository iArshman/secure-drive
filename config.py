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
DATABASE_NAME = "cloudyte_drive_bot"

# Web Server Configuration
PORT = int(os.getenv("PORT", 3000))

# Pagination Settings
FILES_PER_PAGE = 10
ACCOUNTS_PER_PAGE = 10

# ================= LIMITS (CONTROL FROM HERE) =================
# Note: 1 MB = 1024 * 1024 bytes
# Note: 1 GB = 1024 * 1024 * 1024 bytes

# 1. DOWNLOAD LIMIT (Drive -> Telegram)
# Standard Bot API Limit: 50 MB
# Local Server Limit: 2000 MB (2GB)
MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024 

# 2. UPLOAD LIMIT (Telegram -> Drive)
# Standard Bot API Limit: 20 MB

# Local Server Limit: 2000 MB (2GB)
MAX_UPLOAD_SIZE = 20 * 1024 * 1024
