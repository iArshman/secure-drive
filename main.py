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
from aiohttp import web

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

from config import BOT_TOKEN, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, SCOPES, PORT, MAX_DOWNLOAD_SIZE, MAX_UPLOAD_SIZE
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
        ("settings", "Settings"),
        ("addaccount", "Add Drive Account"),
        ("logout", "Logout")
    ]]
    await bot.set_my_commands(commands)

# ============= HELPERS =============

async def get_current_user_id(telegram_id: int) -> Optional[int]:
    """Get internal user ID for currently logged in user"""
    return await db.get_internal_user_id(telegram_id)

def get_file_view(mime_type: str, name: str) -> str:
    if mime_type == 'application/vnd.google-apps.folder': 
        return f"📁 {name}"
    if "." in name and not name.endswith("."):
        return name
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

        results = service.files().list(q=query, pageSize=100, pageToken=page_token, fields="files(id, name, mimeType, size), nextPageToken").execute()
        raw_files = results.get('files', [])
        next_pt = results.get('nextPageToken')

        processed_files = []
        for f in raw_files:
            real_name = decrypt_name(f['name'])
            if search_query and search_query.lower() not in real_name.lower(): continue
            processed_files.append({'id': f['id'], 'name': real_name, 'mimeType': f['mimeType'], 'size': f.get('size')})

        processed_files.sort(key=lambda x: (x['mimeType'] != 'application/vnd.google-apps.folder', x['name'].lower()))

        title = "Root"
        if folder_id != "root":
            try:
                meta = service.files().get(fileId=folder_id, fields='name').execute()
                title = decrypt_name(meta.get('name'))
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
                btn_text = get_file_view(f['mimeType'], f['name'])
                if len(btn_text) > 20: btn_text = btn_text[:20] + ".."
                row.append(InlineKeyboardButton(text=btn_text, callback_data=f"info:{h}"))
            keyboard.append(row)

        if next_pt:
            nh = await store_file_data(account_id, folder_id, folder_id, next_pt)
            keyboard.append([InlineKeyboardButton(text="Next Page", callback_data=f"page:{nh}")])

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
        logger.error(f"UI Error: {e}")
        err_msg = "Error fetching files."
        if isinstance(event, CallbackQuery): await event.answer(err_msg)
        else: await event.answer(err_msg)

async def render_file_info(callback: CallbackQuery, h: str):
    f_data = await db.callback_data.find_one({"hash": h})
    if f_data:
        acc = await db.accounts.find_one({"_id": ObjectId(f_data['account_id'])})
        service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
        f = service.files().get(fileId=f_data['file_id'], fields="id, name, size, mimeType, modifiedTime").execute()
        real_name = decrypt_name(f['name'])
        
        text = (f"<b>File Details</b>\nAccount: <code>{escape_html(acc['email'])}</code>\n━━━━━━━━━━━━━━━━━━\n"
                f"<b>Name:</b> {escape_html(real_name)}\n<b>Size:</b> {format_file_size(f.get('size'))}\n<b>Date:</b> {f.get('modifiedTime')[:10]}")
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Download", callback_data=f"down:{h}")],
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
    
    text = f"<b>Settings</b>\n\n<b>Default Account:</b>\n{escape_html(default['email'])}\n"
    
    # Get storage for default account
    try:
        service = get_drive_service(default['access_token'], default.get('refresh_token'))
        about = service.about().get(fields="storageQuota").execute()
        quota = about.get('storageQuota', {})
        usage = int(quota.get('usage', 0))
        limit = int(quota.get('limit', 0))
        text += f"Storage: {usage/(1024**3):.2f} GB / {limit/(1024**3):.2f} GB\n\n"
    except:
        text += "Storage: Unable to fetch\n\n"
    
    if backup:
        backup_status = "ON" if backup_enabled else "OFF"
        text += f"<b>Backup Account:</b> [{backup_status}]\n{escape_html(backup['email'])}\n"
        
        # Get storage for backup account
        try:
            backup_service = get_drive_service(backup['access_token'], backup.get('refresh_token'))
            backup_about = backup_service.about().get(fields="storageQuota").execute()
            backup_quota = backup_about.get('storageQuota', {})
            backup_usage = int(backup_quota.get('usage', 0))
            backup_limit = int(backup_quota.get('limit', 0))
            text += f"Storage: {backup_usage/(1024**3):.2f} GB / {backup_limit/(1024**3):.2f} GB\n\n"
        except:
            text += "Storage: Unable to fetch\n\n"
    else:
        text += "<b>Backup Account:</b>\nNot set\n\n"
    
    text += "<i>Click an account to manage:</i>"
    
    kb = []
    for acc in accounts:
        is_def = "[✓] " if acc.get('_id') == default.get('_id') else ""
        kb.append([InlineKeyboardButton(text=f"{is_def}{acc['email']}", callback_data=f"sett_acc:{acc['_id']}")])
    
    # Backup controls
    backup_row = []
    backup_row.append(InlineKeyboardButton(text="Set Backup Account", callback_data="set_backup"))
    if backup:
        toggle_text = "Disable Backup" if backup_enabled else "Enable Backup"
        backup_row.append(InlineKeyboardButton(text=toggle_text, callback_data="toggle_backup"))
    kb.append(backup_row)
    
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if isinstance(event, Message): await event.answer(text, reply_markup=markup, parse_mode="HTML")
    else: await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

# ============= COMMANDS =============

async def cmd_start(message: Message):
    user_id = message.from_user.id
    
    # Check if user is logged in
    if await db.is_user_logged_in(user_id):
        await message.answer(
            "<b>Secure Drive</b>\n\n"
            "/files - File Manager\n"
            "/upload - Secure Upload\n"
            "/search - Search Files\n"
            "/settings - Manage Accounts\n"
            "/addaccount - Link Drive\n"
            "/logout - Logout",
            parse_mode="HTML"
        )
    else:
        # User not logged in - show register/login options
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Register", callback_data="auth_register")],
            [InlineKeyboardButton(text="Login", callback_data="auth_login")]
        ])
        await message.answer(
            "<b>Welcome to Secure Drive</b>\n\n"
            "Please register or login to continue.",
            reply_markup=kb,
            parse_mode="HTML"
        )

async def cmd_files(message: Message):
    if not await db.is_user_logged_in(message.from_user.id):
        return await message.answer("Please login first using /start")
    
    internal_id = await get_current_user_id(message.from_user.id)
    if not internal_id:
        return await message.answer("Please login first using /start")
    
    acc = await db.accounts.find_one({"user_id": internal_id, "is_default": True}) or await db.accounts.find_one({"user_id": internal_id})
    if not acc: return await message.answer("No account linked. Use /addaccount")
    await render_explorer(message, str(acc['_id']), "root")

async def cmd_search(message: Message):
    if not await db.is_user_logged_in(message.from_user.id):
        return await message.answer("Please login first using /start")
    
    user_states[message.from_user.id] = {"action": "search"}
    await message.answer("Enter the file name to search:")

async def cmd_upload(message: Message):
    if not await db.is_user_logged_in(message.from_user.id):
        return await message.answer("Please login first using /start")
    
    user_states[message.from_user.id] = {"action": "upload_file", "parent_id": "root"}
    await message.answer("Send any file (Video/Audio/Photo/Doc) now:", parse_mode="HTML")

async def cmd_storage(message: Message):
    if not await db.is_user_logged_in(message.from_user.id):
        return await message.answer("Please login first using /start")
    
    user_id = message.from_user.id
    acc = await db.accounts.find_one({"user_id": user_id, "is_default": True}) or await db.accounts.find_one({"user_id": user_id})
    if not acc: return await message.answer("No account connected.")
    try:
        service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
        about = service.about().get(fields="storageQuota").execute()
        quota = about.get('storageQuota', {})
        usage = int(quota.get('usage', 0)); limit = int(quota.get('limit', 0))
        await message.answer(f"<b>Storage:</b> {usage/(1024**3):.2f} GB / {limit/(1024**3):.2f} GB", parse_mode="HTML")
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
    oauth_states[state_key] = {"user_id": internal_id, "telegram_id": message.from_user.id}
    flow = Flow.from_client_config(
        {"web": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(access_type='offline', state=state_key, prompt='consent')
    await message.answer("Link Account:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Connect Google Drive", url=auth_url)]]))

async def cmd_logout(message: Message):
    user_id = message.from_user.id
    if await db.logout_user(user_id):
        # Clear user state
        if user_id in user_states:
            del user_states[user_id]
        await message.answer("<b>Logged out successfully.</b>\n\nUse /start to login again.", parse_mode="HTML")
    else:
        await message.answer("You are not logged in.")

# ============= CALLBACKS =============

async def handle_callback(callback: CallbackQuery):
    data = callback.data
    telegram_id = callback.from_user.id

    # === Authentication Callbacks ===
    if data == "auth_register":
        user_states[telegram_id] = {"action": "register_username"}
        await callback.message.edit_text(
            "<b>Registration</b>\n\nEnter your desired username:",
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    elif data == "auth_login":
        user_states[telegram_id] = {"action": "login_username"}
        await callback.message.edit_text(
            "<b>Login</b>\n\nEnter your username:",
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    # Check if user is logged in for other callbacks
    if not await db.is_user_logged_in(telegram_id):
        await callback.answer("Please login first using /start", show_alert=True)
        return
    
    # Get internal user ID
    user_id = await get_current_user_id(telegram_id)
    if not user_id:
        await callback.answer("Please login first using /start", show_alert=True)
        return

    if data.startswith("open:"):
        h = data.split(":")[1]
        f_data = await db.callback_data.find_one({"hash": h})
        if f_data: await render_explorer(callback, f_data['account_id'], f_data['file_id'])

    elif data.startswith("page:"):
        h = data.split(":")[1]
        f_data = await db.callback_data.find_one({"hash": h})
        if f_data: await render_explorer(callback, f_data['account_id'], f_data['file_id'], f_data['next_token'])

    elif data.startswith("info:"):
        await render_file_info(callback, data.split(":")[1])

    elif data.startswith("del:"):
        h = data.split(":")[1]
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Yes", callback_data=f"del_yes:{h}"), InlineKeyboardButton(text="No", callback_data=f"del_no:{h}")]])
        await callback.message.edit_text("Delete this file?", reply_markup=kb, parse_mode="HTML")

    elif data.startswith("del_yes:"):
        h = data.split(":")[1]
        f_data = await db.callback_data.find_one({"hash": h})
        acc = await db.accounts.find_one({"_id": ObjectId(f_data['account_id'])})
        try:
            get_drive_service(acc['access_token'], acc.get('refresh_token')).files().delete(fileId=f_data['file_id']).execute()
            await callback.answer("Deleted successfully")
            await render_explorer(callback, f_data['account_id'], f_data['parent_id'])
        except Exception as e:
            await callback.answer(f"Failed: {e}", show_alert=True)

    elif data.startswith("del_no:"):
        h = data.split(":")[1]
        await render_file_info(callback, h)

    elif data.startswith("down:"):
        h = data.split(":")[1]
        f_data = await db.callback_data.find_one({"hash": h})
        acc = await db.accounts.find_one({"_id": ObjectId(f_data['account_id'])})
        service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
        f_meta = service.files().get(fileId=f_data['file_id'], fields="name, size").execute()
        real_name = decrypt_name(f_meta['name'])
        
        file_size = int(f_meta.get('size', 0))
        if file_size > MAX_DOWNLOAD_SIZE:
            return await callback.answer(f"File too big! Limit is {MAX_DOWNLOAD_SIZE//(1024*1024)}MB", show_alert=True)
        
        await callback.answer("Downloading...", show_alert=False)
        request = service.files().get_media(fileId=f_data['file_id'])
        file_io = io.BytesIO()
        downloader = MediaIoBaseDownload(file_io, request)
        done = False
        while not done: _, done = downloader.next_chunk()
        file_io.seek(0)
        decrypted_bytes = decrypt_data(file_io.read())
        await callback.message.answer_document(BufferedInputFile(decrypted_bytes, filename=real_name))

    elif data.startswith("ren:"):
        user_states[user_id] = {"action": "rename", "hash": data.split(":")[1]}
        await callback.message.answer("Enter new name:")

    elif data.startswith("mkdir:"):
        user_states[user_id] = {"action": "create_folder", "parent_id": data.split(":")[1]}
        await callback.message.answer("Enter Folder Name:")

    elif data.startswith("up:"):
        user_states[user_id] = {"action": "upload_file", "parent_id": data.split(":")[1]}
        await callback.message.answer("Send file now:")

    # Batch Upload Handler
    elif data.startswith("batch_up:"):
        folder_id = data.split(":")[1]
        user_states[user_id] = {"action": "batch_upload", "parent_id": folder_id}
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Done", callback_data=f"batch_done:{folder_id}")]])
        await callback.message.answer("<b>Batch Mode Active</b>\n\nSend unlimited files. When finished, click Done.", reply_markup=kb, parse_mode="HTML")
        await callback.answer()

    # [NEW] BATCH DONE HANDLER
    elif data.startswith("batch_done:"):
        folder_id = data.split(":")[1]
        if user_id in user_states:
            del user_states[user_id]
        
        # Determine account_id (User Default)
        acc = await db.accounts.find_one({"user_id": user_id, "is_default": True})
        if acc:
            await callback.message.delete() # Remove the "Batch Mode Active" message
            await render_explorer(callback.message, str(acc['_id']), folder_id)

    elif data.startswith("open_parent:"):
        h = data.split(":")[1]
        f_data = await db.callback_data.find_one({"hash": h})
        await render_explorer(callback, f_data['account_id'], f_data['parent_id'])

    elif data == "go_root":
        acc = await db.accounts.find_one({"user_id": user_id, "is_default": True})
        await render_explorer(callback, str(acc['_id']), "root")
    
    elif data.startswith("sett_acc:"):
        acc_id = data.split(":")[1]
        kb = [[InlineKeyboardButton(text="Make Default", callback_data=f"mk_def:{acc_id}")],
              [InlineKeyboardButton(text="Delete", callback_data=f"rm_acc:{acc_id}")],
              [InlineKeyboardButton(text="← Back", callback_data="back_set")]]
        await callback.message.edit_text("Manage Account:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

    elif data.startswith("mk_def:"):
        await db.accounts.update_many({"user_id": user_id}, {"$set": {"is_default": False}})
        await db.accounts.update_one({"_id": ObjectId(data.split(":")[1])}, {"$set": {"is_default": True}})
        await callback.answer("Updated successfully")
        await render_settings(callback, user_id)

    elif data.startswith("rm_acc:"):
        await db.accounts.delete_one({"_id": ObjectId(data.split(":")[1])})
        await callback.answer("Removed successfully")
        await render_settings(callback, user_id)
    
    elif data == "back_set":
        await render_settings(callback, user_id)
    
    elif data == "set_backup":
        user_states[telegram_id] = {"action": "set_backup_email"}
        await callback.message.answer("Enter the email of the account you want to set as backup:")
        await callback.answer()
        return
    
    elif data == "toggle_backup":
        current_status = await db.is_backup_enabled(user_id)
        await db.toggle_backup(user_id, not current_status)
        await callback.answer(f"Backup {'enabled' if not current_status else 'disabled'}")
        await render_settings(callback, user_id)

    await callback.answer()

# ============= UPLOAD & INPUT =============

async def handle_user_input(message: Message):
    telegram_id = message.from_user.id
    state = user_states.get(telegram_id)
    if not state: return

    # === Authentication Input Handlers ===
    if state['action'] == "register_username":
        username = message.text.strip()
        if len(username) < 3:
            return await message.answer("Username must be at least 3 characters long.")
        user_states[telegram_id] = {"action": "register_password", "username": username}
        await message.answer("Enter your password (min 6 characters):")
        return
    
    elif state['action'] == "register_password":
        password = message.text.strip()
        if len(password) < 6:
            return await message.answer("Password must be at least 6 characters long.")
        
        username = state['username']
        result = await db.register_user(
            telegram_id,
            username,
            password,
            message.from_user.full_name
        )
        
        if result['success']:
            del user_states[telegram_id]
            await message.answer(
                "<b>Registration successful!</b>\n\n"
                f"Account: <b>{username}</b>\n\n"
                "You are now logged in. Use /start to see available commands.",
                parse_mode="HTML"
            )
        else:
            del user_states[telegram_id]
            if result['error'] == 'username_taken':
                await message.answer("Username is already taken. Try a different username.\n\nUse /start to try again.")
            else:
                await message.answer("Registration failed. Try /start again.")
        return
    
    elif state['action'] == "login_username":
        username = message.text.strip()
        user_states[telegram_id] = {"action": "login_password", "username": username}
        await message.answer("Enter your password:")
        return
    
    elif state['action'] == "login_password":
        password = message.text.strip()
        username = state['username']
        
        result = await db.login_user(telegram_id, username, password)
        
        if result['success']:
            del user_states[telegram_id]
            await message.answer(
                "<b>Login successful!</b>\n\n"
                f"Account: <b>{username}</b>\n\n"
                "Use /start to see available commands.",
                parse_mode="HTML"
            )
        else:
            del user_states[telegram_id]
            await message.answer("Login failed. Invalid username or password. Try /start again.")
        return
    
    # Check if user is logged in for other actions
    if not await db.is_user_logged_in(telegram_id):
        return await message.answer("Please login first using /start")
    
    # Get internal user ID for drive operations
    user_id = await get_current_user_id(telegram_id)
    if not user_id:
        return await message.answer("Please login first using /start")
    
    # Handle backup account setup
    if state['action'] == "set_backup_email":
        email = message.text.strip()
        
        # Check if account exists
        existing_acc = await db.get_account_by_email(user_id, email)
        
        if existing_acc:
            # Account found, set as backup
            await db.set_backup_account(user_id, str(existing_acc['_id']))
            del user_states[telegram_id]
            await message.answer(
                f"<b>Backup account set:</b>\n{escape_html(email)}\n\n"
                "Use /settings to enable/disable backup.",
                parse_mode="HTML"
            )
        else:
            # Account not found, provide auth link
            state_key = f"{telegram_id}_{int(datetime.now().timestamp())}_backup"
            oauth_states[state_key] = {"user_id": user_id, "telegram_id": telegram_id, "is_backup": True}
            flow = Flow.from_client_config(
                {"web": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}},
                scopes=SCOPES, redirect_uri=REDIRECT_URI
            )
            auth_url, _ = flow.authorization_url(access_type='offline', state=state_key, prompt='consent')
            del user_states[telegram_id]
            await message.answer(
                f"Account with email <code>{escape_html(email)}</code> not found.\n\n"
                "Click below to add this account:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Connect as Backup Account", url=auth_url)]]),
                parse_mode="HTML"
            )
        return

    acc = await db.accounts.find_one({"user_id": user_id, "is_default": True}) or await db.accounts.find_one({"user_id": user_id})
    if not acc:
        return await message.answer("No account linked. Use /addaccount")
    
    service = get_drive_service(acc['access_token'], acc.get('refresh_token'))

    if state['action'] == "search":
        del user_states[message.from_user.id]
        await render_explorer(message, str(acc['_id']), "root", search_query=message.text)

    elif state['action'] == "rename":
        f_data = await db.callback_data.find_one({"hash": state['hash']})
        service.files().update(fileId=f_data['file_id'], body={'name': encrypt_name(message.text)}).execute()
        await message.answer("Renamed successfully"); del user_states[user_id]
        await render_explorer(message, f_data['account_id'], f_data['parent_id'])

    elif state['action'] == "create_folder":
        meta = {'name': encrypt_name(message.text), 'mimeType': 'application/vnd.google-apps.folder', 'parents': [state['parent_id']] if state['parent_id'] != "root" else []}
        service.files().create(body=meta).execute()
        await message.answer("Folder created successfully"); del user_states[user_id]
        await render_explorer(message, str(acc['_id']), state['parent_id'])

    # [CHANGE] Unified Handler for Single Upload and Batch Upload
    elif state['action'] in ["upload_file", "batch_upload"]:
        file_obj = None; filename = "untitled"
        if message.document: file_obj = message.document; filename = message.document.file_name
        elif message.video: file_obj = message.video; filename = message.video.file_name or f"video_{message.message_id}.mp4"
        elif message.audio: file_obj = message.audio; filename = message.audio.file_name or f"audio_{message.message_id}.mp3"
        elif message.photo: file_obj = message.photo[-1]; filename = f"photo_{message.message_id}.jpg"

        if file_obj:
            if file_obj.file_size > MAX_UPLOAD_SIZE:
                await message.answer(f"File too big! Limit is {MAX_UPLOAD_SIZE//(1024*1024)}MB")
                return

            msg = await message.reply(f"Uploading <b>{escape_html(filename)}</b>...", parse_mode="HTML")
            try:
                file_io = await bot.download(file_obj)
                file_bytes = file_io.read()
                enc_bytes = encrypt_data(file_bytes)
                enc_name = encrypt_name(filename)
                meta = {'name': enc_name, 'parents': [state['parent_id']] if state['parent_id'] != "root" else []}
                media = MediaIoBaseUpload(io.BytesIO(enc_bytes), mimetype='application/octet-stream')
                service.files().create(body=meta, media_body=media).execute()
                
                # Check if backup is enabled and upload to backup account
                if await db.is_backup_enabled(user_id):
                    backup_acc = await db.get_backup_account(user_id)
                    if backup_acc:
                        try:
                            backup_service = get_drive_service(backup_acc['access_token'], backup_acc.get('refresh_token'))
                            backup_media = MediaIoBaseUpload(io.BytesIO(enc_bytes), mimetype='application/octet-stream')
                            backup_service.files().create(body=meta, media_body=backup_media).execute()
                            await msg.edit_text("Uploaded successfully (+ backup copy)")
                        except Exception as backup_error:
                            logger.error(f"Backup upload failed: {backup_error}")
                            await msg.edit_text("Uploaded successfully (backup failed)")
                    else:
                        await msg.edit_text("Uploaded successfully")
                else:
                    await msg.edit_text("Uploaded successfully")
                
                # Only clear state if it is SINGLE upload
                if state['action'] == "upload_file":
                    del user_states[user_id]
                    await render_explorer(message, str(acc['_id']), state['parent_id'])
                
                # If Batch, keep state active
                
            except Exception as e:
                await msg.edit_text(f"Error: {e}")

# ============= MAIN =============

async def main():
    global bot, db, dp
    bot = Bot(token=BOT_TOKEN)
    db = Database()
    dp = Dispatcher()
    
    try:
        key = await db.get_or_create_encryption_key()
        init_cipher(key)
        logger.info("Cipher initialized successfully")
    except Exception as e:
        logger.error(f"Encryption key error: {e}")
        return

    await set_bot_commands(bot)
    logger.info("Bot commands registered")

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_files, Command("files"))
    dp.message.register(cmd_search, Command("search"))
    dp.message.register(cmd_upload, Command("upload"))
    dp.message.register(cmd_settings, Command("settings"))
    dp.message.register(cmd_add, Command("addaccount"))
    dp.message.register(cmd_logout, Command("logout"))
    
    dp.message.register(handle_user_input, F.text | F.document | F.video | F.audio | F.photo)
    dp.callback_query.register(handle_callback)
    
    web_module.setup_web_module(bot, db, oauth_states, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
    app = web_module.create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    logger.info(f"Web server started on port {PORT}")
    
    logger.info("Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
