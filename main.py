import os
import logging
import asyncio
import html
import hashlib
import io
from datetime import datetime, timezone
from typing import Optional, Dict
from bson import ObjectId

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, BotCommand
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from config import (
    BOT_TOKEN, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, SCOPES,
    PORT, MAX_DOWNLOAD_SIZE, MAX_UPLOAD_SIZE, USE_LOCAL_SERVER, LOCAL_SERVER_URL
)
from database import Database
from crypto import encrypt_data, decrypt_data, encrypt_name, decrypt_name, init_cipher
import web as web_module

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Global vars
bot: Optional[Bot] = None
db: Optional[Database] = None
dp: Optional[Dispatcher] = None
oauth_states: Dict[str, dict] = {}
user_states: Dict[int, dict] = {}

# ============= MENU SETUP =============

async def set_bot_commands(bot: Bot):
    commands = [BotCommand(command=c, description=d) for c, d in [
        ("start", "Start Bot"),
        ("files", "File Manager"),
        ("search", "Search Files"),
        ("upload", "Secure Upload"),
        ("storage", "Check Storage"),
        ("settings", "Settings"),
        ("addaccount", "Add Drive Account"),
        ("logout", "Logout")
    ]]
    await bot.set_my_commands(commands)

# ============= HELPERS =============

async def get_current_user_id(telegram_id: int) -> Optional[int]:
    return await db.get_internal_user_id(telegram_id)

async def enc(user_id: int) -> bool:
    return await db.is_encryption_enabled(user_id)

def get_file_icon(mime_type: str) -> str:
    if mime_type == 'application/vnd.google-apps.folder':
        return "📁"
    if mime_type.startswith("image/"):
        return "🖼️"
    if mime_type.startswith("video/"):
        return "🎬"
    if mime_type.startswith("audio/"):
        return "🎵"
    if "pdf" in mime_type:
        return "📄"
    if "zip" in mime_type or "rar" in mime_type or "archive" in mime_type:
        return "🗜️"
    return "📎"

def get_file_view(mime_type: str, name: str) -> str:
    icon = get_file_icon(mime_type)
    return f"{icon} {name}"

def format_file_size(size_bytes):
    if not size_bytes:
        return "0 B"
    size_bytes = int(size_bytes)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

def escape_html(text: str) -> str:
    return html.escape(str(text))

async def store_file_data(account_id: str, file_id: str, parent_id: str = "root", next_token: str = None) -> str:
    hash_value = hashlib.md5(f"{account_id}:{file_id}:{parent_id}:{next_token}".encode()).hexdigest()[:16]
    await db.callback_data.update_one(
        {"hash": hash_value},
        {"$set": {
            "hash": hash_value,
            "account_id": account_id,
            "file_id": file_id,
            "parent_id": parent_id,
            "next_token": next_token,
            "created_at": datetime.now(timezone.utc)
        }},
        upsert=True
    )
    return hash_value

def get_drive_service(access_token: str, refresh_token: str = None):
    """Build Drive service, auto-refreshing token if expired."""
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES
    )
    # Refresh if expired and a refresh token is available
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            logger.warning(f"Token refresh failed: {e}")
    return build('drive', 'v3', credentials=creds, cache_discovery=False)

async def refresh_and_save_token(account_id: str, access_token: str, refresh_token: str) -> Optional[str]:
    """Refresh token and persist updated access_token to DB. Returns new access_token or None."""
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES
    )
    try:
        creds.refresh(Request())
        await db.accounts.update_one(
            {"_id": ObjectId(account_id)},
            {"$set": {
                "access_token": creds.token,
                "expires_at": creds.expiry.timestamp() if creds.expiry else None,
                "updated_at": datetime.now(timezone.utc)
            }}
        )
        return creds.token
    except Exception as e:
        logger.error(f"Failed to refresh token for account {account_id}: {e}")
        return None

# ============= RENDERERS =============

async def render_explorer(event, account_id: str, folder_id: str = "root", page_token: str = None, search_query: str = None):
    try:
        account = await db.accounts.find_one({"_id": ObjectId(account_id)})
        if not account:
            err = "Account not found."
            if isinstance(event, Message):
                await event.answer(err)
            else:
                await event.answer(err, show_alert=True)
            return

        service = get_drive_service(account['access_token'], account.get('refresh_token'))
        enc_on = await enc(account['user_id'])

        # Build query
        if search_query:
            # Search across entire drive, not just current folder
            query = "trashed=false"
        else:
            query = f"'{folder_id}' in parents and trashed=false"

        results = service.files().list(
            q=query,
            pageSize=100,
            pageToken=page_token,
            fields="files(id, name, mimeType, size), nextPageToken",
            orderBy="folder,name"
        ).execute()
        raw_files = results.get('files', [])
        next_pt = results.get('nextPageToken')

        processed_files = []
        for f in raw_files:
            real_name = decrypt_name(f['name'], enc_on)
            if search_query and search_query.lower() not in real_name.lower():
                continue
            processed_files.append({
                'id': f['id'],
                'name': real_name,
                'mimeType': f['mimeType'],
                'size': f.get('size')
            })

        # Folders first, then files alphabetically (Drive API orderBy handles this too)
        processed_files.sort(key=lambda x: (x['mimeType'] != 'application/vnd.google-apps.folder', x['name'].lower()))

        title = "🗂 Root"
        if search_query:
            title = f"🔍 Results: {escape_html(search_query)}"
        elif folder_id != "root":
            try:
                meta = service.files().get(fileId=folder_id, fields='name').execute()
                title = f"📁 {decrypt_name(meta.get('name'), enc_on)}"
            except Exception:
                title = "📁 Folder"

        email = escape_html(account.get('email', 'Unknown'))
        text = f"<b>{title}</b>\n<code>{email}</code>\n━━━━━━━━━━━━━━━━━━\n"

        if not processed_files:
            text += "<i>No files found.</i>"

        keyboard = []

        # Folders
        for f in [x for x in processed_files if x['mimeType'] == 'application/vnd.google-apps.folder']:
            h = await store_file_data(account_id, f['id'], folder_id)
            keyboard.append([InlineKeyboardButton(
                text=get_file_view(f['mimeType'], f['name'][:30]),
                callback_data=f"open:{h}"
            )])

        # Files in rows of 2
        files_list = [x for x in processed_files if x['mimeType'] != 'application/vnd.google-apps.folder']
        for i in range(0, len(files_list), 2):
            row = []
            for f in files_list[i:i+2]:
                h = await store_file_data(account_id, f['id'], folder_id)
                btn_text = f['name']
                if len(btn_text) > 22:
                    btn_text = btn_text[:20] + ".."
                row.append(InlineKeyboardButton(
                    text=get_file_view(f['mimeType'], btn_text),
                    callback_data=f"info:{h}"
                ))
            keyboard.append(row)

        # Pagination
        if next_pt:
            nh = await store_file_data(account_id, folder_id, folder_id, next_pt)
            keyboard.append([InlineKeyboardButton(text="▶ Next Page", callback_data=f"page:{nh}")])

        # Controls
        if not search_query:
            controls = []
            if folder_id != "root":
                controls.append(InlineKeyboardButton(text="⬅ Back", callback_data="go_root"))
            controls.append(InlineKeyboardButton(text="📤 Upload", callback_data=f"up:{folder_id}"))
            controls.append(InlineKeyboardButton(text="📦 Batch", callback_data=f"batch_up:{folder_id}"))
            controls.append(InlineKeyboardButton(text="📁 New Folder", callback_data=f"mkdir:{folder_id}"))
            keyboard.append(controls)
        else:
            keyboard.append([InlineKeyboardButton(text="⬅ Back to Root", callback_data="go_root")])

        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        if isinstance(event, Message):
            await event.answer(text, reply_markup=markup, parse_mode="HTML")
        else:
            try:
                await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
            except TelegramBadRequest:
                pass  # Message not modified

    except Exception as e:
        logger.error(f"render_explorer error: {e}", exc_info=True)
        err_msg = "⚠️ Error loading files. Please try again."
        if isinstance(event, Message):
            await event.answer(err_msg)
        elif isinstance(event, CallbackQuery):
            await event.answer(err_msg, show_alert=True)


async def render_file_info(callback: CallbackQuery, h: str):
    f_data = await db.callback_data.find_one({"hash": h})
    if not f_data:
        await callback.answer("File data expired. Please refresh.", show_alert=True)
        return

    acc = await db.accounts.find_one({"_id": ObjectId(f_data['account_id'])})
    if not acc:
        await callback.answer("Account not found.", show_alert=True)
        return

    enc_on = await enc(acc['user_id'])
    service = get_drive_service(acc['access_token'], acc.get('refresh_token'))

    try:
        f = service.files().get(
            fileId=f_data['file_id'],
            fields="id, name, size, mimeType, modifiedTime, webViewLink"
        ).execute()
    except Exception as e:
        await callback.answer(f"Error fetching file: {e}", show_alert=True)
        return

    real_name = decrypt_name(f['name'], enc_on)
    mod_time = f.get('modifiedTime', '')[:10] if f.get('modifiedTime') else 'Unknown'

    text = (
        f"<b>📄 File Details</b>\n"
        f"Account: <code>{escape_html(acc['email'])}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>Name:</b> {escape_html(real_name)}\n"
        f"<b>Size:</b> {format_file_size(f.get('size'))}\n"
        f"<b>Type:</b> {escape_html(f.get('mimeType', 'Unknown'))}\n"
        f"<b>Modified:</b> {mod_time}"
    )

    bot_decrypt_on = await db.is_bot_decrypt_enabled(acc['user_id'])
    download_row = [InlineKeyboardButton(text="⬇️ Download", callback_data=f"down:{h}")]

    # Show "Decrypt & Download" only when encryption was ON (file was encrypted) and bot decrypt is ON
    if enc_on and bot_decrypt_on:
        download_row.append(InlineKeyboardButton(text="🔓 Decrypt & DL", callback_data=f"down_dec:{h}"))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        download_row,
        [
            InlineKeyboardButton(text="✏️ Rename", callback_data=f"ren:{h}"),
            InlineKeyboardButton(text="🗑️ Delete", callback_data=f"del:{h}")
        ],
        [InlineKeyboardButton(text="⬅ Back", callback_data=f"open_parent:{h}")]
    ])

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass


async def render_settings(event, user_id: int):
    accounts = await db.accounts.find({"user_id": user_id}).to_list(length=20)
    if not accounts:
        msg = "<b>No accounts found.</b>\nUse /addaccount to link a Google Drive."
        if isinstance(event, Message):
            await event.answer(msg, parse_mode="HTML")
        else:
            await event.message.edit_text(msg, parse_mode="HTML")
        return

    default = await db.accounts.find_one({"user_id": user_id, "is_default": True}) or accounts[0]
    backup = await db.get_backup_account(user_id)
    backup_enabled = await db.is_backup_enabled(user_id)
    enc_enabled = await db.is_encryption_enabled(user_id)
    bot_decrypt_enabled = await db.is_bot_decrypt_enabled(user_id)

    enc_status_text = "🔒 ON" if enc_enabled else "🔓 OFF"
    text = f"<b>⚙️ Settings</b>\n\n"
    text += f"<b>Encryption:</b> {enc_status_text}\n"
    if enc_enabled:
        text += "<i>Files are encrypted on upload. Downloads are auto-decrypted.</i>\n\n"
    else:
        text += "<i>Files are stored as-is.</i>\n\n"

    text += f"<b>Default Account:</b>\n{escape_html(default['email'])}\n"

    try:
        service = get_drive_service(default['access_token'], default.get('refresh_token'))
        about = service.about().get(fields="storageQuota").execute()
        quota = about.get('storageQuota', {})
        usage = int(quota.get('usage', 0))
        limit = int(quota.get('limit', 0))
        text += f"Storage: {usage/(1024**3):.2f} GB / {limit/(1024**3):.2f} GB\n\n"
    except Exception:
        text += "Storage: Unable to fetch\n\n"

    if backup:
        backup_status = "ON" if backup_enabled else "OFF"
        text += f"<b>Backup Account:</b> [{backup_status}]\n{escape_html(backup['email'])}\n"
        try:
            backup_service = get_drive_service(backup['access_token'], backup.get('refresh_token'))
            backup_about = backup_service.about().get(fields="storageQuota").execute()
            bq = backup_about.get('storageQuota', {})
            bu = int(bq.get('usage', 0))
            bl = int(bq.get('limit', 0))
            text += f"Storage: {bu/(1024**3):.2f} GB / {bl/(1024**3):.2f} GB\n\n"
        except Exception:
            text += "Storage: Unable to fetch\n\n"
    else:
        text += "<b>Backup Account:</b>\nNot set\n\n"

    text += "<i>Click an account to manage:</i>"

    kb = []
    enc_btn_text = "🔒 Encryption: ON" if enc_enabled else "🔓 Encryption: OFF"
    kb.append([InlineKeyboardButton(text=enc_btn_text, callback_data="toggle_encryption")])

    backup_row = [InlineKeyboardButton(text="Set Backup Account", callback_data="set_backup")]
    if backup:
        toggle_text = "Disable Backup" if backup_enabled else "Enable Backup"
        backup_row.append(InlineKeyboardButton(text=toggle_text, callback_data="toggle_backup"))
    kb.append(backup_row)

    bot_dec_text = "🤖 Bot Decrypt: ON" if bot_decrypt_enabled else "🤖 Bot Decrypt: OFF"
    kb.append([InlineKeyboardButton(text=bot_dec_text, callback_data="toggle_bot_decrypt")])

    kb.append([InlineKeyboardButton(text="── Accounts ──", callback_data="noop")])
    for acc in accounts:
        is_def = "✓ " if acc.get('_id') == default.get('_id') else ""
        kb.append([InlineKeyboardButton(
            text=f"{is_def}{acc['email']}",
            callback_data=f"sett_acc:{acc['_id']}"
        )])

    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    try:
        if isinstance(event, Message):
            await event.answer(text, reply_markup=markup, parse_mode="HTML")
        else:
            await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest:
        pass

# ============= COMMANDS =============

async def cmd_start(message: Message):
    telegram_id = message.from_user.id

    if await db.is_user_logged_in(telegram_id):
        await message.answer(
            "<b>☁️ Secure Drive</b>\n\n"
            "/files — File Manager\n"
            "/upload — Upload File\n"
            "/search — Search Files\n"
            "/storage — Check Storage\n"
            "/settings — Manage Accounts\n"
            "/addaccount — Link Drive\n"
            "/logout — Logout",
            parse_mode="HTML"
        )
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Register", callback_data="auth_register")],
            [InlineKeyboardButton(text="🔐 Login", callback_data="auth_login")]
        ])
        await message.answer(
            "<b>Welcome to Secure Drive</b>\n\nPlease register or login to continue.",
            reply_markup=kb,
            parse_mode="HTML"
        )

async def cmd_files(message: Message):
    telegram_id = message.from_user.id
    if not await db.is_user_logged_in(telegram_id):
        return await message.answer("Please login first using /start")

    internal_id = await get_current_user_id(telegram_id)
    if not internal_id:
        return await message.answer("Please login first using /start")

    acc = (
        await db.accounts.find_one({"user_id": internal_id, "is_default": True})
        or await db.accounts.find_one({"user_id": internal_id})
    )
    if not acc:
        return await message.answer("No account linked. Use /addaccount")
    await render_explorer(message, str(acc['_id']), "root")

async def cmd_search(message: Message):
    if not await db.is_user_logged_in(message.from_user.id):
        return await message.answer("Please login first using /start")
    user_states[message.from_user.id] = {"action": "search"}
    await message.answer("🔍 Enter file name to search:")

async def cmd_upload(message: Message):
    if not await db.is_user_logged_in(message.from_user.id):
        return await message.answer("Please login first using /start")

    internal_id = await get_current_user_id(message.from_user.id)
    if not internal_id:
        return await message.answer("Please login first using /start")

    acc = (
        await db.accounts.find_one({"user_id": internal_id, "is_default": True})
        or await db.accounts.find_one({"user_id": internal_id})
    )
    if not acc:
        return await message.answer("No account linked. Use /addaccount")

    user_states[message.from_user.id] = {"action": "upload_file", "parent_id": "root", "account_id": str(acc['_id'])}
    await message.answer(
        f"📤 Send any file now.\n<i>Limit: {MAX_UPLOAD_SIZE // (1024*1024)} MB</i>",
        parse_mode="HTML"
    )

async def cmd_storage(message: Message):
    if not await db.is_user_logged_in(message.from_user.id):
        return await message.answer("Please login first using /start")

    user_id = await get_current_user_id(message.from_user.id)
    if not user_id:
        return

    acc = (
        await db.accounts.find_one({"user_id": user_id, "is_default": True})
        or await db.accounts.find_one({"user_id": user_id})
    )
    if not acc:
        return await message.answer("No account connected.")

    try:
        service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
        about = service.about().get(fields="storageQuota").execute()
        quota = about.get('storageQuota', {})
        usage = int(quota.get('usage', 0))
        limit = int(quota.get('limit', 0))
        pct = (usage / limit * 100) if limit else 0
        await message.answer(
            f"<b>💾 Storage</b>\n"
            f"{usage/(1024**3):.2f} GB / {limit/(1024**3):.2f} GB ({pct:.1f}%)",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"Error: {str(e)}")

async def cmd_settings(message: Message):
    if not await db.is_user_logged_in(message.from_user.id):
        return await message.answer("Please login first using /start")

    internal_id = await get_current_user_id(message.from_user.id)
    if not internal_id:
        return await message.answer("Please login first using /start")
    await render_settings(message, internal_id)

async def cmd_add(message: Message):
    if not await db.is_user_logged_in(message.from_user.id):
        return await message.answer("Please login first using /start")

    internal_id = await get_current_user_id(message.from_user.id)
    if not internal_id:
        return await message.answer("Please login first using /start")

    state_key = f"{message.from_user.id}_{int(datetime.now().timestamp())}"
    flow = Flow.from_client_config(
        {"web": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }},
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state_key
    )
    oauth_states[state_key] = {"user_id": internal_id, "telegram_id": message.from_user.id, "flow": flow}

    await message.answer(
        "🔗 Link your Google Drive account:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Connect Google Drive", url=auth_url)
        ]])
    )

async def cmd_logout(message: Message):
    telegram_id = message.from_user.id
    if await db.logout_user(telegram_id):
        user_states.pop(telegram_id, None)
        await message.answer("<b>Logged out successfully.</b>\n\nUse /start to login again.", parse_mode="HTML")
    else:
        await message.answer("You are not logged in.")

# ============= CALLBACKS =============

async def handle_callback(callback: CallbackQuery):
    data = callback.data
    telegram_id = callback.from_user.id

    # Protect active upload state from being interrupted by other callbacks
    current_state = user_states.get(telegram_id)
    if current_state and current_state.get('action') in ["upload_file", "batch_upload"]:
        if not data.startswith("batch_done"):
            await callback.answer("⚠️ Please finish your upload first!", show_alert=True)
            return

    # Auth callbacks (no login required)
    if data == "auth_register":
        user_states[telegram_id] = {"action": "register_username"}
        await callback.message.edit_text("<b>📝 Registration</b>\n\nEnter a username (min 3 chars):", parse_mode="HTML")
        await callback.answer()
        return

    elif data == "auth_login":
        user_states[telegram_id] = {"action": "login_username"}
        await callback.message.edit_text("<b>🔐 Login</b>\n\nEnter your username:", parse_mode="HTML")
        await callback.answer()
        return

    # All other callbacks require login
    if not await db.is_user_logged_in(telegram_id):
        await callback.answer("Please login first using /start", show_alert=True)
        return

    user_id = await get_current_user_id(telegram_id)
    if not user_id:
        await callback.answer("Please login first using /start", show_alert=True)
        return

    if data.startswith("open:"):
        h = data.split(":", 1)[1]
        f_data = await db.callback_data.find_one({"hash": h})
        if f_data:
            await render_explorer(callback, f_data['account_id'], f_data['file_id'])
        else:
            await callback.answer("Session expired, please refresh.", show_alert=True)

    elif data.startswith("page:"):
        h = data.split(":", 1)[1]
        f_data = await db.callback_data.find_one({"hash": h})
        if f_data:
            await render_explorer(callback, f_data['account_id'], f_data['file_id'], f_data.get('next_token'))
        else:
            await callback.answer("Session expired, please refresh.", show_alert=True)

    elif data.startswith("info:"):
        await render_file_info(callback, data.split(":", 1)[1])

    elif data.startswith("del:"):
        h = data.split(":", 1)[1]
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Yes, Delete", callback_data=f"del_yes:{h}"),
            InlineKeyboardButton(text="❌ No", callback_data=f"del_no:{h}")
        ]])
        await callback.message.edit_text("🗑️ Are you sure you want to delete this file?", reply_markup=kb)
        await callback.answer()

    elif data.startswith("del_yes:"):
        h = data.split(":", 1)[1]
        f_data = await db.callback_data.find_one({"hash": h})
        if not f_data:
            await callback.answer("Data expired.", show_alert=True)
            return
        acc = await db.accounts.find_one({"_id": ObjectId(f_data['account_id'])})
        if not acc:
            await callback.answer("Account not found.", show_alert=True)
            return
        try:
            get_drive_service(acc['access_token'], acc.get('refresh_token')).files().delete(
                fileId=f_data['file_id']
            ).execute()
            await callback.answer("✅ Deleted successfully")
            await render_explorer(callback, f_data['account_id'], f_data['parent_id'])
        except Exception as e:
            await callback.answer(f"Failed: {e}", show_alert=True)

    elif data.startswith("del_no:"):
        await render_file_info(callback, data.split(":", 1)[1])

    elif data.startswith("down:"):
        h = data.split(":", 1)[1]
        await handle_download(callback, h, force_decrypt=False)

    elif data.startswith("down_dec:"):
        h = data.split(":", 1)[1]
        await handle_download(callback, h, force_decrypt=True)

    elif data.startswith("ren:"):
        user_states[telegram_id] = {"action": "rename", "hash": data.split(":", 1)[1]}
        await callback.message.answer("✏️ Enter new name:")
        await callback.answer()

    elif data.startswith("mkdir:"):
        user_states[telegram_id] = {"action": "create_folder", "parent_id": data.split(":", 1)[1]}
        await callback.message.answer("📁 Enter folder name:")
        await callback.answer()

    elif data.startswith("up:"):
        folder_id = data.split(":", 1)[1]
        acc = (
            await db.accounts.find_one({"user_id": user_id, "is_default": True})
            or await db.accounts.find_one({"user_id": user_id})
        )
        if not acc:
            await callback.answer("No account linked.", show_alert=True)
            return
        user_states[telegram_id] = {
            "action": "upload_file",
            "parent_id": folder_id,
            "account_id": str(acc['_id'])
        }
        await callback.message.answer(
            f"📤 Send a file to upload.\n<i>Limit: {MAX_UPLOAD_SIZE // (1024*1024)} MB</i>",
            parse_mode="HTML"
        )
        await callback.answer()

    elif data.startswith("batch_up:"):
        folder_id = data.split(":", 1)[1]
        acc = (
            await db.accounts.find_one({"user_id": user_id, "is_default": True})
            or await db.accounts.find_one({"user_id": user_id})
        )
        if not acc:
            await callback.answer("No account linked.", show_alert=True)
            return
        user_states[telegram_id] = {
            "action": "batch_upload",
            "parent_id": folder_id,
            "account_id": str(acc['_id'])
        }
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Done", callback_data=f"batch_done:{folder_id}")
        ]])
        await callback.message.answer(
            "<b>📦 Batch Upload Active</b>\n\nSend files one by one. Click Done when finished.",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()

    elif data.startswith("batch_done:"):
        folder_id = data.split(":", 1)[1]
        user_states.pop(telegram_id, None)
        acc = await db.accounts.find_one({"user_id": user_id, "is_default": True})
        if acc:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await render_explorer(callback, str(acc['_id']), folder_id)
        await callback.answer("Batch upload complete!")

    elif data.startswith("open_parent:"):
        h = data.split(":", 1)[1]
        f_data = await db.callback_data.find_one({"hash": h})
        if f_data:
            await render_explorer(callback, f_data['account_id'], f_data['parent_id'])
        else:
            await callback.answer("Data expired.", show_alert=True)

    elif data == "go_root":
        acc = (
            await db.accounts.find_one({"user_id": user_id, "is_default": True})
            or await db.accounts.find_one({"user_id": user_id})
        )
        if acc:
            await render_explorer(callback, str(acc['_id']), "root")
        else:
            await callback.answer("No account found.", show_alert=True)

    elif data.startswith("sett_acc:"):
        acc_id = data.split(":", 1)[1]
        kb = [[
            InlineKeyboardButton(text="⭐ Make Default", callback_data=f"mk_def:{acc_id}"),
            InlineKeyboardButton(text="🗑️ Delete", callback_data=f"rm_acc:{acc_id}")
        ], [
            InlineKeyboardButton(text="⬅ Back", callback_data="back_set")
        ]]
        await callback.message.edit_text("Manage Account:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
        await callback.answer()

    elif data.startswith("mk_def:"):
        acc_id = data.split(":", 1)[1]
        await db.accounts.update_many({"user_id": user_id}, {"$set": {"is_default": False}})
        await db.accounts.update_one({"_id": ObjectId(acc_id)}, {"$set": {"is_default": True}})
        await db.update_user(user_id, {"default_account_id": acc_id})
        await callback.answer("✅ Default account updated")
        await render_settings(callback, user_id)

    elif data.startswith("rm_acc:"):
        acc_id = data.split(":", 1)[1]
        await db.accounts.delete_one({"_id": ObjectId(acc_id)})
        # If this was the default, set another one
        remaining = await db.accounts.find_one({"user_id": user_id})
        if remaining:
            await db.set_default_account(user_id, str(remaining['_id']))
        await callback.answer("✅ Account removed")
        await render_settings(callback, user_id)

    elif data == "back_set":
        await render_settings(callback, user_id)

    elif data == "set_backup":
        user_states[telegram_id] = {"action": "set_backup_email"}
        await callback.message.answer("Enter the email of the backup account:")
        await callback.answer()

    elif data == "toggle_backup":
        current_status = await db.is_backup_enabled(user_id)
        await db.toggle_backup(user_id, not current_status)
        await callback.answer(f"Backup {'enabled ✅' if not current_status else 'disabled ❌'}")
        await render_settings(callback, user_id)

    elif data == "toggle_encryption":
        current_enc = await db.is_encryption_enabled(user_id)
        await db.toggle_encryption(user_id, not current_enc)
        status = "enabled 🔒" if not current_enc else "disabled 🔓"
        await callback.answer(f"Encryption {status}")
        await render_settings(callback, user_id)

    elif data == "toggle_bot_decrypt":
        current = await db.is_bot_decrypt_enabled(user_id)
        await db.toggle_bot_decrypt(user_id, not current)
        await callback.answer(f"Bot Decrypt {'enabled ✅' if not current else 'disabled ❌'}")
        await render_settings(callback, user_id)

    elif data == "noop":
        await callback.answer()
        return

    else:
        await callback.answer()
        return

    # Final answer to avoid "loading" spinner on unhandled paths
    try:
        await callback.answer()
    except Exception:
        pass


async def handle_download(callback: CallbackQuery, h: str, force_decrypt: bool = False):
    """Unified download handler for normal and force-decrypt downloads."""
    f_data = await db.callback_data.find_one({"hash": h})
    if not f_data:
        await callback.answer("Data expired. Please refresh the file list.", show_alert=True)
        return

    acc = await db.accounts.find_one({"_id": ObjectId(f_data['account_id'])})
    if not acc:
        await callback.answer("Account not found.", show_alert=True)
        return

    service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
    enc_on = await enc(acc['user_id'])

    try:
        f_meta = service.files().get(fileId=f_data['file_id'], fields="name, size, mimeType").execute()
    except Exception as e:
        await callback.answer(f"Error: {e}", show_alert=True)
        return

    # Determine decryption: use force_decrypt flag OR normal enc_on
    should_decrypt = force_decrypt or enc_on
    real_name = decrypt_name(f_meta['name'], should_decrypt)

    file_size = int(f_meta.get('size', 0))
    if file_size > MAX_DOWNLOAD_SIZE:
        await callback.answer(
            f"File too big! Max is {MAX_DOWNLOAD_SIZE // (1024*1024)} MB",
            show_alert=True
        )
        return

    await callback.answer("⬇️ Downloading...", show_alert=False)

    status_msg = await callback.message.answer("⬇️ Downloading, please wait...")

    try:
        request = service.files().get_media(fileId=f_data['file_id'])
        file_io = io.BytesIO()
        downloader = MediaIoBaseDownload(file_io, request, chunksize=4 * 1024 * 1024)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        file_io.seek(0)
        raw_bytes = file_io.read()
        final_bytes = decrypt_data(raw_bytes, should_decrypt)

        await callback.message.answer_document(
            BufferedInputFile(final_bytes, filename=real_name),
            caption=f"📄 <b>{escape_html(real_name)}</b>\n{format_file_size(len(final_bytes))}",
            parse_mode="HTML"
        )
        await status_msg.delete()

    except Exception as e:
        logger.error(f"Download error: {e}", exc_info=True)
        await status_msg.edit_text(f"⚠️ Download failed: {e}")

# ============= UPLOAD & INPUT =============

async def handle_user_input(message: Message):
    telegram_id = message.from_user.id
    state = user_states.get(telegram_id)
    if not state:
        return

    action = state.get('action')

    # --- Auth flows (no login required) ---
    if action == "register_username":
        username = message.text.strip() if message.text else ""
        if len(username) < 3:
            return await message.answer("Username must be at least 3 characters.")
        user_states[telegram_id] = {"action": "register_password", "username": username}
        await message.answer("Enter a password (min 6 characters):")
        return

    elif action == "register_password":
        password = message.text.strip() if message.text else ""
        if len(password) < 6:
            return await message.answer("Password must be at least 6 characters.")
        username = state['username']
        result = await db.register_user(telegram_id, username, password, message.from_user.full_name)
        user_states.pop(telegram_id, None)
        if result['success']:
            await message.answer(
                f"<b>✅ Registration successful!</b>\n\nAccount: <b>{escape_html(username)}</b>\n\nUse /start to see commands.",
                parse_mode="HTML"
            )
        else:
            if result.get('error') == 'username_taken':
                await message.answer("❌ Username already taken. Try /start again.")
            else:
                await message.answer("❌ Registration failed. Try /start again.")
        return

    elif action == "login_username":
        username = message.text.strip() if message.text else ""
        user_states[telegram_id] = {"action": "login_password", "username": username}
        await message.answer("Enter your password:")
        return

    elif action == "login_password":
        password = message.text.strip() if message.text else ""
        username = state['username']
        result = await db.login_user(telegram_id, username, password)
        user_states.pop(telegram_id, None)
        if result['success']:
            await message.answer(
                f"<b>✅ Login successful!</b>\n\nAccount: <b>{escape_html(username)}</b>\n\nUse /start for commands.",
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Invalid username or password. Use /start to try again.")
        return

    # --- All actions below require login ---
    if not await db.is_user_logged_in(telegram_id):
        return await message.answer("Please login first using /start")

    user_id = await get_current_user_id(telegram_id)
    if not user_id:
        return await message.answer("Please login first using /start")

    if action == "set_backup_email":
        email = message.text.strip() if message.text else ""
        existing_acc = await db.get_account_by_email(user_id, email)
        if existing_acc:
            await db.set_backup_account(user_id, str(existing_acc['_id']))
            user_states.pop(telegram_id, None)
            await message.answer(
                f"<b>✅ Backup account set:</b>\n{escape_html(email)}\n\nUse /settings to enable backup.",
                parse_mode="HTML"
            )
        else:
            state_key = f"{telegram_id}_{int(datetime.now().timestamp())}_backup"
            flow = Flow.from_client_config(
                {"web": {
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token"
                }},
                scopes=SCOPES,
                redirect_uri=REDIRECT_URI
            )
            auth_url, _ = flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent",
                state=state_key
            )
            oauth_states[state_key] = {"user_id": user_id, "telegram_id": telegram_id, "is_backup": True, "flow": flow}
            user_states.pop(telegram_id, None)
            await message.answer(
                f"Account <code>{escape_html(email)}</code> not found.\nConnect it below:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Connect as Backup", url=auth_url)
                ]]),
                parse_mode="HTML"
            )
        return

    if action == "search":
        user_states.pop(telegram_id, None)
        acc = (
            await db.accounts.find_one({"user_id": user_id, "is_default": True})
            or await db.accounts.find_one({"user_id": user_id})
        )
        if not acc:
            return await message.answer("No account linked.")
        await render_explorer(message, str(acc['_id']), "root", search_query=message.text)
        return

    elif action == "rename":
        f_data = await db.callback_data.find_one({"hash": state['hash']})
        if not f_data:
            user_states.pop(telegram_id, None)
            return await message.answer("Session expired. Please retry.")
        enc_on = await enc(user_id)
        acc = await db.accounts.find_one({"_id": ObjectId(f_data['account_id'])})
        service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
        try:
            service.files().update(
                fileId=f_data['file_id'],
                body={'name': encrypt_name(message.text, enc_on)}
            ).execute()
            await message.answer("✅ Renamed successfully")
        except Exception as e:
            await message.answer(f"⚠️ Rename failed: {e}")
        user_states.pop(telegram_id, None)
        await render_explorer(message, f_data['account_id'], f_data['parent_id'])
        return

    elif action == "create_folder":
        enc_on = await enc(user_id)
        acc = (
            await db.accounts.find_one({"user_id": user_id, "is_default": True})
            or await db.accounts.find_one({"user_id": user_id})
        )
        if not acc:
            user_states.pop(telegram_id, None)
            return await message.answer("No account linked.")
        service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
        parent_id = state['parent_id']
        parents = [parent_id] if parent_id and parent_id != "root" else []
        try:
            service.files().create(body={
                'name': encrypt_name(message.text, enc_on),
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': parents
            }).execute()
            await message.answer("✅ Folder created")
        except Exception as e:
            await message.answer(f"⚠️ Failed: {e}")
        user_states.pop(telegram_id, None)
        await render_explorer(message, str(acc['_id']), parent_id)
        return

    elif action in ["upload_file", "batch_upload"]:
        await handle_file_upload(message, telegram_id, user_id, state)
        return


async def handle_file_upload(message: Message, telegram_id: int, user_id: int, state: dict):
    """Handles file upload for both single and batch modes."""
    file_obj = None
    filename = "untitled"
    mime_type = 'application/octet-stream'

    if message.document:
        file_obj = message.document
        filename = message.document.file_name or f"file_{message.message_id}"
        mime_type = message.document.mime_type or mime_type
    elif message.video:
        file_obj = message.video
        filename = message.video.file_name or f"video_{message.message_id}.mp4"
        mime_type = message.video.mime_type or 'video/mp4'
    elif message.audio:
        file_obj = message.audio
        filename = message.audio.file_name or f"audio_{message.message_id}.mp3"
        mime_type = message.audio.mime_type or 'audio/mpeg'
    elif message.photo:
        file_obj = message.photo[-1]
        filename = f"photo_{message.message_id}.jpg"
        mime_type = 'image/jpeg'
    else:
        # Not a file message; ignore silently (could be text in batch mode)
        return

    if file_obj.file_size > MAX_UPLOAD_SIZE:
        return await message.reply(
            f"⚠️ File too large! Max size is {MAX_UPLOAD_SIZE // (1024*1024)} MB."
        )

    # Determine account
    account_id = state.get('account_id')
    if account_id:
        acc = await db.accounts.find_one({"_id": ObjectId(account_id)})
    else:
        acc = (
            await db.accounts.find_one({"user_id": user_id, "is_default": True})
            or await db.accounts.find_one({"user_id": user_id})
        )

    if not acc:
        return await message.reply("No account linked. Use /addaccount")

    service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
    enc_on = await enc(user_id)
    parent_id = state.get('parent_id', 'root')

    msg = await message.reply(f"⬆️ Uploading <b>{escape_html(filename)}</b>...", parse_mode="HTML")

    try:
        # Download file from Telegram
        file_io = await bot.download(file_obj)
        file_bytes = file_io.read()

        # Encrypt if needed
        upload_bytes = encrypt_data(file_bytes, enc_on)
        upload_name = encrypt_name(filename, enc_on)

        parents = [parent_id] if parent_id and parent_id != "root" else []
        meta = {'name': upload_name, 'parents': parents}

        # Use resumable upload for larger files
        media = MediaIoBaseUpload(
            io.BytesIO(upload_bytes),
            mimetype='application/octet-stream',
            chunksize=4 * 1024 * 1024,
            resumable=True
        )
        service.files().create(body=meta, media_body=media).execute()

        # Backup upload
        backup_status = ""
        if await db.is_backup_enabled(user_id):
            backup_acc = await db.get_backup_account(user_id)
            if backup_acc:
                try:
                    backup_service = get_drive_service(backup_acc['access_token'], backup_acc.get('refresh_token'))
                    backup_media = MediaIoBaseUpload(
                        io.BytesIO(upload_bytes),
                        mimetype='application/octet-stream',
                        chunksize=4 * 1024 * 1024,
                        resumable=True
                    )
                    backup_service.files().create(body=meta, media_body=backup_media).execute()
                    backup_status = " + backup ✅"
                except Exception as be:
                    logger.error(f"Backup upload failed: {be}")
                    backup_status = " (backup failed ⚠️)"

        await msg.edit_text(f"✅ Uploaded: <b>{escape_html(filename)}</b>{backup_status}", parse_mode="HTML")

        # Clear state and refresh explorer only for single upload
        if state['action'] == "upload_file":
            user_states.pop(telegram_id, None)
            await render_explorer(message, str(acc['_id']), parent_id)

    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        await msg.edit_text(f"⚠️ Upload failed: {escape_html(str(e))}", parse_mode="HTML")
        if state['action'] == "upload_file":
            user_states.pop(telegram_id, None)

# ============= MAIN =============

async def main():
    global bot, db, dp

    if USE_LOCAL_SERVER:
        session = AiohttpSession(api=TelegramAPIServer.from_base(LOCAL_SERVER_URL, is_local=True))
        bot = Bot(token=BOT_TOKEN, session=session)
        logger.info(f"Bot using Local Server: {LOCAL_SERVER_URL}")
    else:
        bot = Bot(token=BOT_TOKEN)
        logger.info("Bot using Standard Telegram API")

    db = Database()
    dp = Dispatcher()

    await db.create_indexes()

    try:
        key = await db.get_or_create_encryption_key()
        init_cipher(key)
        logger.info("Cipher initialized")
    except Exception as e:
        logger.error(f"Cipher init failed: {e}")
        return

    await set_bot_commands(bot)

    # Register handlers
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_files, Command("files"))
    dp.message.register(cmd_search, Command("search"))
    dp.message.register(cmd_upload, Command("upload"))
    dp.message.register(cmd_storage, Command("storage"))
    dp.message.register(cmd_settings, Command("settings"))
    dp.message.register(cmd_add, Command("addaccount"))
    dp.message.register(cmd_logout, Command("logout"))
    dp.message.register(handle_user_input, F.text | F.document | F.video | F.audio | F.photo)
    dp.callback_query.register(handle_callback)

    if hasattr(web_module, 'setup_web_module'):
        web_module.setup_web_module(bot, db, oauth_states, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
        app = web_module.create_web_app()
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '0.0.0.0', PORT).start()
        logger.info(f"Web server on port {PORT}")

    logger.info("Bot polling started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
