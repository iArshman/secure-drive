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
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, ContentType
from aiohttp import web

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

from config import BOT_TOKEN, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, SCOPES, PORT, MAX_FILE_SIZE
from database import Database
from crypto import encrypt_data, decrypt_data, encrypt_name, decrypt_name, init_cipher
import web as web_module

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global vars
bot: Optional[Bot] = None
db: Optional[Database] = None
dp: Optional[Dispatcher] = None
oauth_states: Dict[str, dict] = {}
user_states: Dict[int, dict] = {}

# ============= HELPERS =============

def get_file_view(mime_type: str, name: str) -> str:
    """
    Returns the visual representation for the file button.
    - Folders: "📁 Name"
    - Files with Extension: "Name" (Clean)
    - Files without Extension: "📄 Name" (Marked as garbage/unknown)
    """
    if mime_type == 'application/vnd.google-apps.folder': 
        return f"📁 {name}"
    
    # Check if file has an extension (e.g., "video.mp4")
    if "." in name and not name.endswith("."):
        return name  # CLEAN VIEW: No emoji, just filename
        
    # No extension (Garbage file) -> Add emoji
    return f"📄 {name}"

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

# ============= CORE RENDERER =============

async def render_explorer(event, account_id: str, folder_id: str = "root", page_token: str = None, search_query: str = None):
    try:
        account = await db.accounts.find_one({"_id": ObjectId(account_id)})
        service = get_drive_service(account['access_token'], account.get('refresh_token'))

        query = f"'{folder_id}' in parents and trashed=false"
        if search_query:
            query = "trashed=false" 

        results = service.files().list(
            q=query,
            pageSize=100, 
            pageToken=page_token,
            fields="files(id, name, mimeType, size), nextPageToken"
        ).execute()
        
        raw_files = results.get('files', [])
        next_pt = results.get('nextPageToken')

        # Decrypt & Sort
        processed_files = []
        for f in raw_files:
            real_name = decrypt_name(f['name'])
            if search_query and search_query.lower() not in real_name.lower():
                continue
            processed_files.append({
                'id': f['id'], 'name': real_name, 'mimeType': f['mimeType'], 'size': f.get('size')
            })

        processed_files.sort(key=lambda x: (x['mimeType'] != 'application/vnd.google-apps.folder', x['name'].lower()))

        if folder_id == "root":
            title = "🔐 Root (Encrypted)"
        else:
            meta = service.files().get(fileId=folder_id, fields='name').execute()
            title = decrypt_name(meta.get('name'))

        text = f"📂 <b>{escape_html(title)}</b>\n"
        text += f"👤 <code>{escape_html(account.get('email', 'Unknown'))}</code>\n"
        text += "━━━━━━━━━━━━━━━━━━\n"
        if not processed_files: text += "<i>Empty folder.</i>"

        keyboard = []
        
        # Folders
        folders = [f for f in processed_files if f['mimeType'] == 'application/vnd.google-apps.folder']
        for f in folders:
            h = await store_file_data(account_id, f['id'], folder_id)
            # Folders always have emoji
            keyboard.append([InlineKeyboardButton(text=get_file_view(f['mimeType'], f['name']), callback_data=f"open:{h}")])

        # Files (Grid View)
        only_files = [f for f in processed_files if f['mimeType'] != 'application/vnd.google-apps.folder']
        for i in range(0, len(only_files), 2):
            row = []
            for f in only_files[i:i+2]:
                h = await store_file_data(account_id, f['id'], folder_id)
                # Files use Clean View (No emoji)
                btn_text = get_file_view(f['mimeType'], f['name'])
                # Truncate long names to 15 chars for grid
                if len(btn_text) > 15: btn_text = btn_text[:15] + ".."
                
                row.append(InlineKeyboardButton(text=btn_text, callback_data=f"info:{h}"))
            keyboard.append(row)

        if next_pt:
            nh = await store_file_data(account_id, folder_id, folder_id, next_pt)
            keyboard.append([InlineKeyboardButton(text="➡️ Next Page", callback_data=f"page:{nh}")])

        controls = []
        if not search_query:
            if folder_id != "root":
                controls.append(InlineKeyboardButton(text="🔙 Back", callback_data="go_root"))
            controls.append(InlineKeyboardButton(text="➕ New", callback_data=f"mkdir:{folder_id}"))
            controls.append(InlineKeyboardButton(text="⬆️ Upload", callback_data=f"up:{folder_id}"))
            keyboard.append(controls)
        else:
            keyboard.append([InlineKeyboardButton(text="🔙 Back to Root", callback_data="go_root")])

        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        if isinstance(event, Message):
            await event.answer(text, reply_markup=markup, parse_mode="HTML")
        else:
            await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

    except Exception as e:
        logger.error(f"UI Error: {e}")
        err_text = "❌ Error fetching files. Token might be expired."
        if isinstance(event, CallbackQuery): await event.answer(err_text)
        else: await event.answer(err_text)

async def render_file_info(callback: CallbackQuery, h: str):
    f_data = await db.callback_data.find_one({"hash": h})
    if f_data:
        acc = await db.accounts.find_one({"_id": ObjectId(f_data['account_id'])})
        service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
        f = service.files().get(fileId=f_data['file_id'], fields="id, name, size, mimeType, modifiedTime").execute()
        
        real_name = decrypt_name(f['name'])
        
        text = (f"📄 <b>File Details</b>\n"
                f"👤 <code>{escape_html(acc['email'])}</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"<b>Name:</b> {escape_html(real_name)}\n"
                f"<b>Size:</b> {format_file_size(f.get('size'))}\n"
                f"<b>Date:</b> {f.get('modifiedTime')[:10]}")
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📥 Decrypt & Download", callback_data=f"down:{h}")],
            [InlineKeyboardButton(text="✏️ Rename", callback_data=f"ren:{h}"), InlineKeyboardButton(text="🗑 Delete", callback_data=f"del:{h}")],
            [InlineKeyboardButton(text="🔙 Back", callback_data=f"open_parent:{h}")]
        ])
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

# ============= COMMAND HANDLERS =============

async def cmd_start(message: Message):
    user_id = message.from_user.id
    if not await db.get_user(user_id):
        await db.create_user(user_id, message.from_user.username, message.from_user.full_name)
    await message.answer("👋 <b>Welcome to Cloudyte Secure Drive!</b>\n\n/files - File Manager\n/upload - Secure Upload\n/addaccount - Link Drive", parse_mode="HTML")

async def cmd_files(message: Message):
    acc = await db.accounts.find_one({"user_id": message.from_user.id, "is_default": True}) or await db.accounts.find_one({"user_id": message.from_user.id})
    if not acc: return await message.answer("⚠️ No account linked. Use /addaccount")
    await render_explorer(message, str(acc['_id']), "root")

async def cmd_search(message: Message):
    user_states[message.from_user.id] = {"action": "search"}
    await message.answer("🔍 Enter the file name to search:")

async def cmd_upload(message: Message):
    user_states[message.from_user.id] = {"action": "upload_file", "parent_id": "root"}
    await message.answer("📤 Send the file (Document, Video, Audio, or Photo) now:", parse_mode="HTML")

async def cmd_storage(message: Message):
    user_id = message.from_user.id
    acc = await db.accounts.find_one({"user_id": user_id, "is_default": True}) or await db.accounts.find_one({"user_id": user_id})
    if not acc: return await message.answer("No account connected.")
    try:
        service = get_drive_service(acc['access_token'], acc.get('refresh_token'))
        about = service.about().get(fields="storageQuota, user").execute()
        quota = about.get('storageQuota', {})
        usage = int(quota.get('usage', 0)); limit = int(quota.get('limit', 0))
        await message.answer(f"💾 <b>Storage:</b> {usage/(1024**3):.2f} GB / {limit/(1024**3):.2f} GB", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"Error: {str(e)}")

async def cmd_settings(message: Message):
    user_id = message.from_user.id
    accounts = await db.accounts.find({"user_id": user_id}).to_list(length=10)
    if not accounts: return await message.answer("No accounts.")
    default = await db.accounts.find_one({"user_id": user_id, "is_default": True}) or accounts[0]
    text = f"⚙️ <b>Settings</b>\nDefault: {escape_html(default['email'])}"
    kb = []
    for acc in accounts:
        is_def = "✅ " if acc.get('_id') == default.get('_id') else ""
        kb.append([InlineKeyboardButton(text=f"{is_def}{acc['email']}", callback_data=f"sett_acc:{acc['_id']}")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

async def cmd_add(message: Message):
    state_key = f"{message.from_user.id}_{int(datetime.now().timestamp())}"
    oauth_states[state_key] = {"user_id": message.from_user.id}
    flow = Flow.from_client_config(
        {"web": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(access_type='offline', state=state_key, prompt='consent')
    await message.answer("🔐 Link Account:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Connect Google Drive", url=auth_url)]]))

# ============= CALLBACK HANDLERS =============

async def handle_callback(callback: CallbackQuery):
    data = callback.data
    user_id = callback.from_user.id

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
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Yes, Delete", callback_data=f"del_yes:{h}")], [InlineKeyboardButton(text="❌ No, Cancel", callback_data=f"del_no:{h}")]])
        await callback.message.edit_text("⚠️ Are you sure?", reply_markup=kb, parse_mode="HTML")

    elif data.startswith("del_yes:"):
        h = data.split(":")[1]
        f_data = await db.callback_data.find_one({"hash": h})
        acc = await db.accounts.find_one({"_id": ObjectId(f_data['account_id'])})
        try:
            get_drive_service(acc['access_token'], acc.get('refresh_token')).files().delete(fileId=f_data['file_id']).execute()
            await callback.answer("✅ File Deleted!")
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
        if int(f_meta.get('size', 0)) > 50 * 1024 * 1024:
            return await callback.answer("⚠️ File > 50MB. Cannot download.", show_alert=True)
        
        await callback.answer("⏳ Downloading...", show_alert=False)
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
        await callback.message.answer("📝 Enter new name:")

    elif data.startswith("mkdir:"):
        user_states[user_id] = {"action": "create_folder", "parent_id": data.split(":")[1]}
        await callback.message.answer("📁 Enter Folder Name:")

    elif data.startswith("up:"):
        user_states[user_id] = {"action": "upload_file", "parent_id": data.split(":")[1]}
        await callback.message.answer("📤 Send the file now:")

    elif data.startswith("open_parent:"):
        h = data.split(":")[1]
        f_data = await db.callback_data.find_one({"hash": h})
        await render_explorer(callback, f_data['account_id'], f_data['parent_id'])

    elif data == "go_root":
        acc = await db.accounts.find_one({"user_id": user_id, "is_default": True})
        await render_explorer(callback, str(acc['_id']), "root")
    
    elif data.startswith("sett_acc:"):
        acc_id = data.split(":")[1]
        kb = [[InlineKeyboardButton(text="⭐ Make Default", callback_data=f"mk_def:{acc_id}")], [InlineKeyboardButton(text="🗑 Delete", callback_data=f"rm_acc:{acc_id}")], [InlineKeyboardButton(text="🔙 Back", callback_data="back_set")]]
        await callback.message.edit_text("Select Action:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

    elif data.startswith("mk_def:"):
        await db.accounts.update_many({"user_id": user_id}, {"$set": {"is_default": False}})
        await db.accounts.update_one({"_id": ObjectId(data.split(":")[1])}, {"$set": {"is_default": True}})
        await callback.answer("✅ Updated!"); await cmd_settings(callback.message)

    elif data.startswith("rm_acc:"):
        await db.accounts.delete_one({"_id": ObjectId(data.split(":")[1])})
        await callback.answer("🗑 Removed"); await cmd_settings(callback.message)
    
    elif data == "back_set":
        await cmd_settings(callback.message)

    await callback.answer()

# ============= USER INPUT =============

async def handle_user_input(message: Message):
    state = user_states.get(message.from_user.id)
    if not state: return

    acc = await db.accounts.find_one({"user_id": message.from_user.id, "is_default": True}) or await db.accounts.find_one({"user_id": message.from_user.id})
    service = get_drive_service(acc['access_token'], acc.get('refresh_token'))

    if state['action'] == "search":
        del user_states[message.from_user.id]
        await render_explorer(message, str(acc['_id']), "root", search_query=message.text)

    elif state['action'] == "rename":
        f_data = await db.callback_data.find_one({"hash": state['hash']})
        service.files().update(fileId=f_data['file_id'], body={'name': encrypt_name(message.text)}).execute()
        await message.answer(f"✅ Renamed to {message.text}"); del user_states[message.from_user.id]
        await render_explorer(message, f_data['account_id'], f_data['parent_id'])

    elif state['action'] == "create_folder":
        meta = {'name': encrypt_name(message.text), 'mimeType': 'application/vnd.google-apps.folder', 'parents': [state['parent_id']] if state['parent_id'] != "root" else []}
        service.files().create(body=meta).execute()
        await message.answer(f"✅ Folder Created!"); del user_states[message.from_user.id]
        await render_explorer(message, str(acc['_id']), state['parent_id'])

    elif state['action'] == "upload_file":
        file_obj = None; filename = "untitled"; mime = "application/octet-stream"

        if message.document:
            file_obj = message.document; filename = message.document.file_name; mime = message.document.mime_type
        elif message.video:
            file_obj = message.video; filename = message.video.file_name or f"video_{message.message_id}.mp4"; mime = message.video.mime_type
        elif message.audio:
            file_obj = message.audio; filename = message.audio.file_name; mime = message.audio.mime_type
        elif message.photo:
            file_obj = message.photo[-1]; filename = f"photo_{message.message_id}.jpg"; mime = "image/jpeg"

        if file_obj:
            msg = await message.answer(f"⏳ Encrypting & Uploading <b>{escape_html(filename)}</b>...", parse_mode="HTML")
            try:
                file_io = await bot.download(file_obj)
                enc_bytes = encrypt_data(file_io.read())
                meta = {'name': encrypt_name(filename), 'parents': [state['parent_id']] if state['parent_id'] != "root" else []}
                media = MediaIoBaseUpload(io.BytesIO(enc_bytes), mimetype=mime)
                service.files().create(body=meta, media_body=media).execute()
                await msg.edit_text("✅ Secure Upload Complete!")
                del user_states[message.from_user.id]
                await render_explorer(message, str(acc['_id']), state['parent_id'])
            except Exception as e:
                await msg.edit_text(f"❌ Error: {e}")

# ============= MAIN =============

async def main():
    global bot, db, dp
    bot = Bot(token=BOT_TOKEN)
    db = Database()
    dp = Dispatcher()
    
    try:
        key = await db.get_or_create_encryption_key()
        init_cipher(key)
        print("✅ Cipher Initialized")
    except Exception as e:
        print(f"❌ Key Error: {e}"); return

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_files, Command("files"))
    dp.message.register(cmd_search, Command("search"))
    dp.message.register(cmd_upload, Command("upload"))
    dp.message.register(cmd_storage, Command("storage"))
    dp.message.register(cmd_settings, Command("settings"))
    dp.message.register(cmd_add, Command("addaccount"))
    
    # Listen for ALL file types
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
