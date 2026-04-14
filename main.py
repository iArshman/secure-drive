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
from database import db # database.py se global db instance use karein
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

# ============= MENU SETUP =============

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

# ============= HELPERS =============

async def get_current_user_id(telegram_id: int) -> Optional[int]:
    """Get internal user ID for currently logged in user"""
    return await db.get_internal_user_id(telegram_id)

async def enc(user_id: int) -> bool:
    """Returns whether encryption is enabled for this user"""
    return await db.is_encryption_enabled(user_id)

def get_file_view(mime_type: str, name: str) -> str:
    if mime_type == 'application/vnd.google-apps.folder': 
        return f"📁 {name}"
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
        if search_query: query = "trashed=false" 

        enc_on = await enc(account['user_id'])
        results = service.files().list(q=query, pageSize=30, pageToken=page_token, fields="files(id, name, mimeType, size), nextPageToken").execute()
        raw_files = results.get('files', [])
        next_pt = results.get('nextPageToken')

        processed_files = []
        for f in raw_files:
            real_name = decrypt_name(f['name'], enc_on)
            if search_query and search_query.lower() not in real_name.lower(): continue
            processed_files.append({'id': f['id'], 'name': real_name, 'mimeType': f['mimeType'], 'size': f.get('size')})

        processed_files.sort(key=lambda x: (x['mimeType'] != 'application/vnd.google-apps.folder', x['name'].lower()))

        title = "Root"
        if folder_id != "root":
            try:
                meta = service.files().get(fileId=folder_id, fields='name').execute()
                title = decrypt_name(meta.get('name'), enc_on)
            except: pass
        
        text = f"<b>{escape_html(title)}</b>\nAccount: <code>{escape_html(account.get('email', 'Unknown'))}</code>\n━━━━━━━━━━━━━━━━━━\n"
        if not processed_files: text += "<i>Empty folder.</i>"

        keyboard = []
        for f in [x for x in processed_files if x['mimeType'] == 'application/vnd.google-apps.folder']:
            h = await store_file_data(account_id, f['id'], folder_id)
            keyboard.append([InlineKeyboardButton(text=get_file_view(f['mimeType'], f['name']), callback_data=f"open:{h}")])

        files_list = [x for x in processed_files if x['mimeType'] != 'application/vnd.google-apps.folder']
        for i in range(0, len(files_list), 2):
            row = []
            for f in files_list[i:i+2]:
                h = await store_file_data(account_id, f['id'], folder_id)
                btn_text = f['name']
                if len(btn_text) > 20: btn_text = btn_text[:18] + ".."
                row.append(InlineKeyboardButton(text=btn_text, callback_data=f"info:{h}"))
            keyboard.append(row)

        if next_pt:
            nh = await store_file_data(account_id, folder_id, folder_id, next_pt)
            keyboard.append([InlineKeyboardButton(text="Next Page ➡️", callback_data=f"page:{nh}")])

        controls = []
        if not search_query:
            if folder_id != "root": controls.append(InlineKeyboardButton(text="← Back", callback_data="go_root"))
            controls.append(InlineKeyboardButton(text="Batch Upload", callback_data=f"batch_up:{folder_id}"))
            controls.append(InlineKeyboardButton(text="+ New Folder", callback_data=f"mkdir:{folder_id}"))
            controls.append(InlineKeyboardButton(text="↑ Upload", callback_data=f"up:{folder_id}"))
            keyboard.append(controls)
        else:
            keyboard.append([InlineKeyboardButton(text="← Back to Root", callback_data="go_root")])

        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        if isinstance(event, Message): await event.answer(text, reply_markup=markup, parse_mode="HTML")
        else: await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Explorer Error: {e}")
        if isinstance(event, CallbackQuery): await event.answer("Error fetching files.")

async def render_file_info(callback: CallbackQuery, h: str):
    f_data = await db.callback_data.find_one({"hash": h})
    if f_data:
        acc = await db.accounts.find_one({"_id": ObjectId(f_data['account_id'])})
        enc_on = await enc(acc['user_id'])
        service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
        f = service.files().get(fileId=f_data['file_id'], fields="id, name, size, mimeType, modifiedTime").execute()
        real_name = decrypt_name(f['name'], enc_on)
        
        text = (f"<b>File Details</b>\nAccount: <code>{escape_html(acc['email'])}</code>\n━━━━━━━━━━━━━━━━━━\n"
                f"<b>Name:</b> {escape_html(real_name)}\n<b>Size:</b> {format_file_size(f.get('size'))}\n<b>Date:</b> {f.get('modifiedTime')[:10]}")
        
        download_row = [InlineKeyboardButton(text="⬇️ Download", callback_data=f"down:{h}")]
        bot_decrypt_on = await db.is_bot_decrypt_enabled(acc['user_id'])
        if not enc_on and bot_decrypt_on:
            download_row.append(InlineKeyboardButton(text="🔓 Decrypt & Download", callback_data=f"down_dec:{h}"))
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            download_row,
            [InlineKeyboardButton(text="Rename", callback_data=f"ren:{h}"), InlineKeyboardButton(text="Delete", callback_data=f"del:{h}")],
            [InlineKeyboardButton(text="← Back", callback_data=f"open_parent:{h}")]
        ])
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

async def render_settings(event, user_id: int):
    accounts = await db.accounts.find({"user_id": user_id}).to_list(length=10)
    if not accounts:
        msg = "<b>No accounts found.</b>\nUse /addaccount to link a Google Drive."
        if isinstance(event, Message): await event.answer(msg, parse_mode="HTML")
        else: await event.message.edit_text(msg, parse_mode="HTML")
        return

    default = await db.accounts.find_one({"user_id": user_id, "is_default": True}) or accounts[0]
    backup = await db.get_backup_account(user_id)
    backup_enabled = await db.is_backup_enabled(user_id)
    enc_enabled = await db.is_encryption_enabled(user_id)
    bot_decrypt_enabled = await db.is_bot_decrypt_enabled(user_id)

    text = f"<b>⚙️ Settings</b>\n\n"
    text += f"<b>Encryption:</b> {'🔒 ON' if enc_enabled else '🔓 OFF'}\n"
    text += f"<b>Default Account:</b>\n{escape_html(default['email'])}\n"

    # Fetch Storage Info [Re-compared with main (18).py]
    try:
        service = get_drive_service(default['access_token'], default.get('refresh_token'))
        about = service.about().get(fields="storageQuota").execute()
        q = about.get('storageQuota', {})
        usage, limit = int(q.get('usage', 0)), int(q.get('limit', 0))
        text += f"Storage: {usage/(1024**3):.2f} / {limit/(1024**3):.2f} GB\n\n"
    except: text += "Storage: Unable to fetch\n\n"

    if backup:
        backup_status = "ON" if backup_enabled else "OFF"
        text += f"<b>Backup Account:</b> [{backup_status}]\n{escape_html(backup['email'])}\n"
    else:
        text += "<b>Backup Account:</b>\nNot set\n\n"

    kb = []
    kb.append([InlineKeyboardButton(text=f"Encryption: {'ON' if enc_enabled else 'OFF'}", callback_data="toggle_encryption")])
    
    backup_row = [InlineKeyboardButton(text="Set Backup Account", callback_data="set_backup")]
    if backup:
        toggle_text = "Disable Backup" if backup_enabled else "Enable Backup"
        backup_row.append(InlineKeyboardButton(text=toggle_text, callback_data="toggle_backup"))
    kb.append(backup_row)

    if not enc_enabled:
        kb.append([InlineKeyboardButton(text=f"Bot Decrypt: {'ON' if bot_decrypt_enabled else 'OFF'}", callback_data="toggle_bot_decrypt")])

    kb.append([InlineKeyboardButton(text="── Accounts ──", callback_data="noop")])
    for acc in accounts:
        is_def = "✅ " if acc.get('_id') == default.get('_id') else ""
        kb.append([InlineKeyboardButton(text=f"{is_def}{acc['email']}", callback_data=f"sett_acc:{acc['_id']}")])

    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    
    try: # [FIX] TelegramBadRequest: Message is not modified
        if isinstance(event, Message): await event.answer(text, reply_markup=markup, parse_mode="HTML")
        else: await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest: pass

# ============= COMMANDS =============

async def cmd_start(message: Message):
    if await db.is_user_logged_in(message.from_user.id):
        await message.answer("<b>Secure Drive</b>\n\n/files - Manager\n/settings - Settings\n/logout - Logout", parse_mode="HTML")
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Register", callback_data="auth_register")],
            [InlineKeyboardButton(text="Login", callback_data="auth_login")]
        ])
        await message.answer("<b>Welcome</b>\nPlease register or login.", reply_markup=kb, parse_mode="HTML")

async def cmd_files(message: Message):
    uid = await get_current_user_id(message.from_user.id)
    if not uid: return
    acc = await db.accounts.find_one({"user_id": uid, "is_default": True}) or await db.accounts.find_one({"user_id": uid})
    if acc: await render_explorer(message, str(acc['_id']), "root")
    else: await message.answer("No account linked. Use /addaccount")

async def cmd_search(message: Message):
    user_states[message.from_user.id] = {"action": "search"}
    await message.answer("Enter file name to search:")

async def cmd_settings(message: Message):
    uid = await get_current_user_id(message.from_user.id)
    if uid: await render_settings(message, uid)

async def cmd_logout(message: Message):
    if await db.logout_user(message.from_user.id):
        await message.answer("Logged out.")
    else: await message.answer("Not logged in.")

# ============= CALLBACKS =============

async def handle_callback(callback: CallbackQuery):
    data = callback.data
    tid = callback.from_user.id

    # Auth Handlers
    if data == "auth_register":
        user_states[tid] = {"action": "register_username"}
        return await callback.message.edit_text("Enter Username:")
    elif data == "auth_login":
        user_states[tid] = {"action": "login_username"}
        return await callback.message.edit_text("Enter Username:")

    uid = await get_current_user_id(tid)
    if not uid: return await callback.answer("Please login.", show_alert=True)

    if data == "toggle_encryption":
        curr = await db.is_encryption_enabled(uid)
        await db.toggle_encryption(uid, not curr)
        await render_settings(callback, uid)
    elif data == "toggle_backup":
        curr = await db.is_backup_enabled(uid)
        await db.toggle_backup(uid, not curr)
        await render_settings(callback, uid)
    elif data == "toggle_bot_decrypt":
        curr = await db.is_bot_decrypt_enabled(uid)
        await db.toggle_bot_decrypt(uid, not curr)
        await render_settings(callback, uid)
    elif data.startswith("open:"):
        f = await db.callback_data.find_one({"hash": data.split(":")[1]})
        if f: await render_explorer(callback, f['account_id'], f['file_id'])
    elif data.startswith("info:"):
        await render_file_info(callback, data.split(":")[1])
    elif data.startswith("up:"):
        user_states[tid] = {"action": "upload_file", "parent_id": data.split(":")[1]}
        await callback.message.answer("Send file now:")
    elif data.startswith("batch_up:"):
        user_states[tid] = {"action": "batch_upload", "parent_id": data.split(":")[1]}
        await callback.message.answer("Batch Mode Active. Send files. Click Done when finished.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Done", callback_data=f"batch_done:{data.split(':')[1]}")]]))
    elif data.startswith("batch_done:"):
        if tid in user_states: del user_states[tid]
        acc = await db.accounts.find_one({"user_id": uid, "is_default": True})
        await callback.message.delete()
        await render_explorer(callback.message, str(acc['_id']), data.split(":")[1])
    elif data.startswith("del:"):
        h = data.split(":")[1]
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Yes", callback_data=f"del_yes:{h}"), InlineKeyboardButton(text="No", callback_data=f"del_no:{h}")]])
        await callback.message.edit_text("Delete file?", reply_markup=kb)
    elif data.startswith("del_yes:"):
        f = await db.callback_data.find_one({"hash": data.split(":")[1]})
        acc = await db.accounts.find_one({"_id": ObjectId(f['account_id'])})
        get_drive_service(acc['access_token'], acc.get('refresh_token')).files().delete(fileId=f['file_id']).execute()
        await callback.answer("Deleted."); await render_explorer(callback, f['account_id'], f['parent_id'])
    elif data == "go_root":
        acc = await db.accounts.find_one({"user_id": uid, "is_default": True})
        await render_explorer(callback, str(acc['_id']), "root")
    elif data.startswith("sett_acc:"):
        aid = data.split(":")[1]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Make Default", callback_data=f"mk_def:{aid}")],
            [InlineKeyboardButton(text="Delete Account", callback_data=f"rm_acc:{aid}")],
            [InlineKeyboardButton(text="← Back", callback_data="back_set")]
        ])
        await callback.message.edit_text("Account Management:", reply_markup=kb)
    elif data.startswith("mk_def:"):
        await db.set_default_account(uid, data.split(":")[1])
        await callback.answer("Updated."); await render_settings(callback, uid)
    elif data == "back_set":
        await render_settings(callback, uid)

    await callback.answer()

# ============= UPLOAD & INPUT [FULL LOGIC] =============

async def handle_user_input(message: Message):
    tid = message.from_user.id
    state = user_states.get(tid)
    if not state: return

    # Auth logic
    if state['action'] == "register_username":
        user_states[tid] = {"action": "register_password", "username": message.text.strip()}
        return await message.answer("Enter Password:")
    elif state['action'] == "register_password":
        res = await db.register_user(tid, state['username'], message.text.strip())
        if res['success']: await message.answer("Registered!")
        else: await message.answer(f"Failed: {res['error']}")
        del user_states[tid]; return
    elif state['action'] == "login_username":
        user_states[tid] = {"action": "login_password", "username": message.text.strip()}
        return await message.answer("Enter Password:")
    elif state['action'] == "login_password":
        res = await db.login_user(tid, state['username'], message.text.strip())
        if res['success']: await message.answer("Logged in!")
        else: await message.answer("Invalid credentials.")
        del user_states[tid]; return

    uid = await get_current_user_id(tid)
    if not uid: return

    # File Upload Logic [Compared with main (18).py]
    if state['action'] in ["upload_file", "batch_upload"]:
        file_obj = message.document or message.video or message.audio or (message.photo[-1] if message.photo else None)
        if not file_obj: return

        acc = await db.accounts.find_one({"user_id": uid, "is_default": True})
        service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
        filename = getattr(file_obj, 'file_name', f"file_{message.message_id}")
        
        m = await message.reply(f"Uploading <b>{escape_html(filename)}</b>...", parse_mode="HTML")
        try:
            file_io = await bot.download(file_obj)
            content = file_io.read()
            enc_on = await enc(uid)
            
            final_content = encrypt_data(content, enc_on)
            final_name = encrypt_name(filename, enc_on)
            
            meta = {'name': final_name, 'parents': [state['parent_id']] if state['parent_id'] != "root" else []}
            media = MediaIoBaseUpload(io.BytesIO(final_content), mimetype='application/octet-stream')
            service.files().create(body=meta, media_body=media).execute()

            # Backup logic
            if await db.is_backup_enabled(uid):
                b_acc = await db.get_backup_account(uid)
                if b_acc:
                    b_service = get_drive_service(b_acc['access_token'], b_acc.get('refresh_token'))
                    b_service.files().create(body=meta, media_body=MediaIoBaseUpload(io.BytesIO(final_content), mimetype='application/octet-stream')).execute()
            
            await m.edit_text("✅ Success.")
            if state['action'] == "upload_file":
                del user_states[tid]
                await render_explorer(message, str(acc['_id']), state['parent_id'])
        except Exception as e:
            await m.edit_text(f"❌ Error: {e}")

    elif state['action'] == "search":
        del user_states[tid]
        acc = await db.accounts.find_one({"user_id": uid, "is_default": True})
        await render_explorer(message, str(acc['_id']), "root", search_query=message.text)

# ============= MAIN =============

async def main():
    global bot, dp
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    key = await db.get_or_create_encryption_key()
    init_cipher(key)

    await set_bot_commands(bot)
    
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_files, Command("files"))
    dp.message.register(cmd_search, Command("search"))
    dp.message.register(cmd_settings, Command("settings"))
    dp.message.register(cmd_logout, Command("logout"))
    dp.message.register(handle_user_input, F.text | F.document | F.video | F.audio | F.photo)
    dp.callback_query.register(handle_callback)
    
    web_module.setup_web_module(bot, db, oauth_states, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
    app = web_module.create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
