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

# File Upload Settings
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB for Telegram file download
