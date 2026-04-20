# ☁️ Secure Drive Bot

A Telegram bot to manage Google Drive files with optional end-to-end encryption, multi-account support, and automatic backup.

---

## 📐 Architecture

```
User (Telegram)
      │
      ▼
 [Bot Server]  ──────────────────────────────────────────────────┐
  main.py                                                         │
  - File manager                                                  │
  - Upload / Download                                             │
  - Encryption                                                    │
  - POST /tokens  ◄──── oauth.arshman.me sends tokens here       │
      │                                                           │
      ▼                                                           │
  [MongoDB]                                                       │
  - Users                                                         │
  - Drive accounts (tokens)                                       │
  - File metadata cache                                           │
                                                                  │
 [OAuth Service]  oauth.arshman.me                               │
  auth.py                                                         │
  - Stateless, no DB                                              │
  - Generates Google OAuth URL                                    │
  - Exchanges code for tokens                                     │
  - POSTs tokens to bot's /tokens endpoint ────────────────────►─┘
```

---

## 🗂️ Project Structure

```
secure-drive/
├── main.py          # Telegram bot + token receiver web server
├── database.py      # MongoDB models and operations
├── crypto.py        # Fernet encryption helpers
├── config.py        # Environment variable loading
├── web.py           # (Legacy - no longer used, can be deleted)
├── auth.py          # Standalone OAuth bridge service
├── requirements.txt
├── Dockerfile
├── .env             # Your secrets (never commit this)
└── README.md
```

---

## ⚙️ Setup Guide

### 1. Prerequisites

- Python 3.10+
- MongoDB instance (MongoDB Atlas free tier works)
- A Google Cloud project with Drive API enabled
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- A server with a public IP (for the token receiver)

---

### 2. Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable **Google Drive API**:
   - APIs & Services → Library → Search "Google Drive API" → Enable
4. Create OAuth credentials:
   - APIs & Services → Credentials → Create Credentials → OAuth Client ID
   - Application type: **Web application**
   - Authorized redirect URIs: `https://oauth.arshman.me/oauth_callback`
5. Copy your `Client ID` and `Client Secret`

---

### 3. Bot Setup

#### Clone and install

```bash
git clone <your-repo>
cd secure-drive
pip install -r requirements.txt
```

#### Configure `.env`

```env
# Telegram
BOT_TOKEN=your_bot_token_here

# Google OAuth (only needed if running your own OAuth service)
GOOGLE_CLIENT_ID=your_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-your_secret
REDIRECT_URI=https://oauth.arshman.me/oauth_callback

# MongoDB
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority

# Web server port (token receiver)
PORT=1025

# OAuth service URL
OAUTH_SERVICE_URL=https://oauth.arshman.me

# Your bot server's public URL (optional - auto-detected if not set)
# BOT_SERVER_URL=http://YOUR_IP:1025

# Local Telegram API Server (optional, for large file support)
USE_LOCAL_SERVER=False
LOCAL_SERVER_URL=http://localhost:8081
```

> **`BOT_SERVER_URL` is optional.** If not set, the bot will auto-detect your public IP on startup using `api.ipify.org`. You only need to set it manually if auto-detection fails or you're behind a NAT/proxy.

#### Run

```bash
python3 main.py
```

You should see:
```
INFO - Bot initialized using Standard Telegram API
INFO - Cipher initialized successfully
INFO - Auto-detected public URL: http://YOUR_IP:1025
INFO - Token receiver URL: http://YOUR_IP:1025/tokens
INFO - Bot commands registered
INFO - Token receiver listening on port 1025
INFO - Bot is running...
```

---

### 4. OAuth Service Setup (oauth.arshman.me)

The OAuth service (`auth.py`) is a **separate, stateless app** deployed independently.

#### Deploy on Coolify / Docker

1. Create a new service in Coolify pointing to `auth.py`
2. Set environment variables:

```env
GOOGLE_CLIENT_ID=your_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-your_secret
REDIRECT_URI=https://oauth.arshman.me/oauth_callback
PORT=3000
```

3. Assign domain: `oauth.arshman.me`
4. The service exposes:
   - `GET /` — Documentation UI
   - `GET /start-auth?state=BASE64` — Starts OAuth flow
   - `GET /oauth_callback` — Google redirects here after sign-in

#### Dockerfile (for auth.py)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY auth.py requirements_oauth.txt ./
RUN pip install aiohttp python-dotenv
EXPOSE 3000
CMD ["python3", "auth.py"]
```

---

### 5. Firewall / Port Setup

The bot's token receiver (`POST /tokens`) must be reachable from `oauth.arshman.me`.

Open port **1025** on your server:

```bash
# UFW
sudo ufw allow 1025/tcp

# iptables
sudo iptables -A INPUT -p tcp --dport 1025 -j ACCEPT
```

---

### 6. Running with Docker (Bot)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
EXPOSE 1025
CMD ["python3", "main.py"]
```

```bash
docker build -t secure-drive-bot .
docker run -d \
  --env-file .env \
  -p 1025:1025 \
  --name secure-drive-bot \
  secure-drive-bot
```

---

### 7. Running with systemd (recommended for VPS)

```ini
# /etc/systemd/system/secure-drive.service
[Unit]
Description=Secure Drive Telegram Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/bots/secure-drive
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=5
EnvironmentFile=/home/ubuntu/bots/secure-drive/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable secure-drive
sudo systemctl start secure-drive
sudo systemctl status secure-drive
```

---

## 🔐 Encryption

When **Encryption** is enabled in `/settings`:

- Files are encrypted with AES-256 (Fernet) **before** upload
- File names are also encrypted
- On download, files are automatically decrypted
- The encryption key is stored in MongoDB (`system_config` collection)
- **If you lose the key (drop the DB), encrypted files cannot be recovered**

> Enable **Bot Decryption** in settings if you want to manually decrypt files that were previously encrypted.

---

## 📦 Features

| Feature | Command |
|---|---|
| Browse files | `/files` |
| Upload file | `/upload` or send file directly |
| Batch upload | File Manager → Batch Upload |
| Search files | `/search` |
| Check storage | `/storage` |
| Settings & accounts | `/settings` |
| Add Google account | `/addaccount` |
| Logout | `/logout` |

---

## 🛠️ Troubleshooting

### Bot starts but `/tokens` never receives anything
- Check port 1025 is open: `curl -X POST http://YOUR_IP:1025/tokens`
- Check the auto-detected URL in bot logs: `Token receiver URL: ...`
- Override manually in `.env`: `BOT_SERVER_URL=http://YOUR_IP:1025`

### `Address already in use` on port 1025
```bash
fuser -k 1025/tcp
python3 main.py
```

### OAuth callback error: `Handshake Failed`
- Make sure `REDIRECT_URI` in Google Console exactly matches `https://oauth.arshman.me/oauth_callback`
- Check OAuth service logs in Coolify

### Files show encrypted names after disabling encryption
- Old files were uploaded with encryption ON — their names are still encrypted in Drive
- Enable **Bot Decryption** in `/settings` to decrypt them on download

### `Cipher not initialized` error
- MongoDB connection failed on startup — check `MONGO_URI` in `.env`

---

## 🔒 Security Notes

- Never commit `.env` to git — add it to `.gitignore`
- Passwords are hashed with `scrypt` (salted) — not reversible
- OAuth tokens are stored in MongoDB — secure your DB with authentication
- The `/tokens` endpoint has no auth for now — restrict by IP in nginx if needed

---

## 📄 License

MIT
