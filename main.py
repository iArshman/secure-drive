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
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

from config import BOT_TOKEN, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, SCOPES, PORT, MAX_DOWNLOAD_SIZE, MAX_UPLOAD_SIZE
from database import db # database (5).py wala global instance
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
dp: Optional[Dispatcher] = None
oauth_states: Dict[str, dict] = {}
user_states: Dict[int, dict] = {}

# ============= HELPERS =============

async def set_bot_commands(bot: Bot):
    commands = [BotCommand(command=c, description=d) for c, d in [
        ("start", "Start Bot"),
        ("files", "File Manager"),
        ("search", "Search Files"),
        ("settings", "Settings"),
        ("addaccount", "Add Drive Account"),
        ("logout", "Logout")
    ]]
    await bot.set_my_commands(commands)

async def get_current_user_id(telegram_id: int) -> Optional[int]:
    return await db.get_internal_user_id(telegram_id)

async def enc(user_id: int) -> bool:
    return await db.is_encryption_enabled(user_id)

def get_file_view(mime_type: str, name: str) -> str:
    if mime_type == 'application/vnd.google-apps.folder': return f"📁 {name}"
    return name

def format_file_size(size_bytes):
    if not size_bytes: return "0 B"
    size_bytes = int(size_bytes)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0: return f"{size_bytes:.2f} {unit}"
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
    creds = Credentials.from_authorized_user_info({
        'token': access_token, 'refresh_token': refresh_token,
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET
    }, SCOPES)
    return build('drive', 'v3', credentials=creds)

# ============= RENDERERS =============

async def render_explorer(event, account_id: str, folder_id: str = "root", page_token: str = None, search_query: str = None):
    try:
        account = await db.accounts.find_one({"_id": ObjectId(account_id)})
        service = get_drive_service(account['access_token'], account.get('refresh_token'))
        query = f"'{folder_id}' in parents and trashed=false"
        if search_query: query = f"name contains '{search_query}' and trashed=false" 

        enc_on = await enc(account['user_id'])
        results = service.files().list(q=query, pageSize=30, pageToken=page_token, fields="files(id, name, mimeType, size), nextPageToken").execute()
        raw_files = results.get('files', [])
        next_pt = results.get('nextPageToken')

        processed_files = []
        for f in raw_files:
            processed_files.append({'id': f['id'], 'name': decrypt_name(f['name'], enc_on), 'mimeType': f['mimeType'], 'size': f.get('size')})

        processed_files.sort(key=lambda x: (x['mimeType'] != 'application/vnd.google-apps.folder', x['name'].lower()))

        text = f"<b>Drive Explorer</b>\nAccount: <code>{escape_html(account['email'])}</code>\n━━━━━━━━━━━━━━━━━━\n"
        keyboard = []
        for f in processed_files:
            h = await store_file_data(account_id, f['id'], folder_id)
            icon = "📁 " if f['mimeType'] == 'application/vnd.google-apps.folder' else "📄 "
            keyboard.append([InlineKeyboardButton(text=f"{icon}{f['name']}", callback_data=f"{'open' if f['mimeType'] == 'application/vnd.google-apps.folder' else 'info'}:{h}")])

        if next_pt:
            nh = await store_file_data(account_id, folder_id, folder_id, next_pt)
            keyboard.append([InlineKeyboardButton(text="Next Page ➡️", callback_data=f"page:{nh}")])

        controls = [InlineKeyboardButton(text="↑ Upload", callback_data=f"up:{folder_id}"), InlineKeyboardButton(text="Batch", callback_data=f"batch_up:{folder_id}")]
        if folder_id != "root": controls.insert(0, InlineKeyboardButton(text="← Back", callback_data="go_root"))
        keyboard.append(controls)

        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        if isinstance(event, Message): await event.answer(text, reply_markup=markup, parse_mode="HTML")
        else: await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception as e: logger.error(f"Explorer Error: {e}")

async def render_file_info(callback: CallbackQuery, h: str):
    f_data = await db.callback_data.find_one({"hash": h})
    if not f_data: return
    acc = await db.accounts.find_one({"_id": ObjectId(f_data['account_id'])})
    service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
    f = service.files().get(fileId=f_data['file_id'], fields="id, name, size, mimeType").execute()
    
    enc_on = await enc(acc['user_id'])
    real_name = decrypt_name(f['name'], enc_on)
    bot_dec_on = await db.is_bot_decrypt_enabled(acc['user_id'])

    text = f"<b>File Info</b>\nName: {escape_html(real_name)}\nSize: {format_file_size(f.get('size'))}"
    
    kb = []
    # FIX: Download logic & Decrypt button shart
    kb.append([InlineKeyboardButton(text="⬇️ Download", callback_data=f"down:{h}")])
    if bot_dec_on: # Button only shows if bot decryption is ON
        kb.append([InlineKeyboardButton(text="🔓 Decrypt & Download", callback_data=f"down_dec:{h}")])
    
    kb.append([InlineKeyboardButton(text="Rename", callback_data=f"ren:{h}"), InlineKeyboardButton(text="Delete", callback_data=f"del:{h}")])
    kb.append([InlineKeyboardButton(text="← Back", callback_data=f"open_parent:{h}")])
    
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

async def render_settings(event, user_id: int):
    accounts = await db.accounts.find({"user_id": user_id}).to_list(length=10)
    if not accounts: return # (No account message here)
    default = await db.accounts.find_one({"user_id": user_id, "is_default": True}) or accounts[0]
    
    enc_enabled = await db.is_encryption_enabled(user_id)
    bot_dec_enabled = await db.is_bot_decrypt_enabled(user_id)
    backup_enabled = await db.is_backup_enabled(user_id)

    text = f"<b>⚙️ Settings</b>\nEncryption: {'🔒 ON' if enc_enabled else '🔓 OFF'}\nDefault: {default['email']}\n"
    
    # Storage logic
    try:
        service = get_drive_service(default['access_token'], default.get('refresh_token'))
        q = service.about().get(fields="storageQuota").execute()['storageQuota']
        text += f"Storage: {int(q['usage'])/(1024**3):.2f}/{int(q['limit'])/(1024**3):.2f} GB"
    except: text += "Storage: N/A"

    kb = [
        [InlineKeyboardButton(text=f"Encryption: {'ON' if enc_enabled else 'OFF'}", callback_data="toggle_encryption")],
        [InlineKeyboardButton(text=f"Backup: {'ON' if backup_enabled else 'OFF'}", callback_data="toggle_backup")],
        [InlineKeyboardButton(text=f"Bot Decrypt: {'ON' if bot_dec_enabled else 'OFF'}", callback_data="toggle_bot_decrypt")],
        [InlineKeyboardButton(text="── Accounts ──", callback_data="noop")]
    ]
    for acc in accounts:
        is_def = "✅ " if acc.get('_id') == default.get('_id') else ""
        kb.append([InlineKeyboardButton(text=f"{is_def}{acc['email']}", callback_data=f"sett_acc:{acc['_id']}")])

    try:
        if isinstance(event, Message): await event.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
        else: await event.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except TelegramBadRequest: pass # Prevent crash on same state

# ============= CALLBACKS =============

async def handle_callback(callback: CallbackQuery):
    data = callback.data
    tid = callback.from_user.id
    uid = await get_current_user_id(tid)

    # FIX: Check if we are in upload state. If so, ignore other button clicks to prevent state loss
    if tid in user_states and user_states[tid]['action'] in ["upload_file", "batch_upload"]:
        if data != f"batch_done:{user_states[tid].get('parent_id')}":
            return await callback.answer("Please finish upload first!", show_alert=True)

    if data.startswith("auth_"):
        if data == "auth_register": user_states[tid] = {"action": "register_username"}
        elif data == "auth_login": user_states[tid] = {"action": "login_username"}
        await callback.message.edit_text("Enter Username:")
        return

    if not uid: return await callback.answer("Login required.")

    # Settings Toggles
    if data == "toggle_encryption":
        await db.toggle_encryption(uid, not await db.is_encryption_enabled(uid))
        await render_settings(callback, uid)
    elif data == "toggle_bot_decrypt":
        await db.toggle_bot_decrypt(uid, not await db.is_bot_decrypt_enabled(uid))
        await render_settings(callback, uid)
    
    # File Operations
    elif data.startswith("open:"):
        f = await db.callback_data.find_one({"hash": data.split(":")[1]})
        await render_explorer(callback, f['account_id'], f['file_id'])
    elif data.startswith("info:"):
        await render_file_info(callback, data.split(":")[1])
    elif data.startswith("up:"):
        user_states[tid] = {"action": "upload_file", "parent_id": data.split(":")[1]}
        await callback.message.answer("Send file now:")
    
    # FIX: Download Logic
    elif data.startswith("down"):
        h = data.split(":")[1]
        f_data = await db.callback_data.find_one({"hash": h})
        acc = await db.accounts.find_one({"_id": ObjectId(f_data['account_id'])})
        service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
        
        await callback.answer("Downloading...")
        request = service.files().get_media(fileId=f_data['file_id'])
        file_io = io.BytesIO()
        downloader = MediaIoBaseDownload(file_io, request)
        done = False
        while not done: _, done = downloader.next_chunk()
        file_io.seek(0)
        
        # Check if we should decrypt (force=True for down_dec)
        should_dec = True if "down_dec" in data else await enc(uid)
        final_bytes = decrypt_data(file_io.read(), should_dec)
        
        meta = service.files().get(fileId=f_data['file_id'], fields="name").execute()
        final_name = decrypt_name(meta['name'], should_dec)
        await callback.message.answer_document(BufferedInputFile(final_bytes, filename=final_name))

    await callback.answer()

# ============= UPLOAD & INPUT =============

async def handle_user_input(message: Message):
    tid = message.from_user.id
    state = user_states.get(tid)
    if not state: return

    # Registration/Login Logic...
    if state['action'] == "register_username":
        user_states[tid] = {"action": "register_password", "username": message.text.strip()}
        return await message.answer("Enter Password:")
    
    # [FIX] Upload logic with protection against button interruptions
    uid = await get_current_user_id(tid)
    if state['action'] in ["upload_file", "batch_upload"]:
        file_obj = message.document or message.video or message.audio or (message.photo[-1] if message.photo else None)
        if not file_obj: return

        acc = await db.accounts.find_one({"user_id": uid, "is_default": True})
        service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
        
        m = await message.reply("Uploading...")
        try:
            file_io = await bot.download(file_obj)
            content = file_io.read()
            enc_on = await enc(uid)
            
            final_content = encrypt_data(content, enc_on)
            final_name = encrypt_name(getattr(file_obj, 'file_name', 'file'), enc_on)
            
            meta = {'name': final_name, 'parents': [state['parent_id']] if state['parent_id'] != "root" else []}
            media = MediaIoBaseUpload(io.BytesIO(final_content), mimetype='application/octet-stream')
            service.files().create(body=meta, media_body=media).execute()
            
            await m.edit_text("✅ Uploaded.")
            # Clear state only for single upload
            if state['action'] == "upload_file": del user_states[tid]
        except Exception as e: await m.edit_text(f"Error: {e}")

# ============= MAIN =============

async def main():
    global bot, dp
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    key = await db.get_or_create_encryption_key()
    init_cipher(key)

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_files, Command("files"))
    dp.message.register(cmd_settings, Command("settings"))
    dp.message.register(handle_user_input, F.text | F.document | F.video | F.audio | F.photo)
    dp.callback_query.register(handle_callback)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
