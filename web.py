"""
Web server module for Cloudyte - OAuth callbacks, Professional Home Page, and Legal Pages
"""
import os
import time
import json
import logging
import base64
from aiohttp import web, ClientSession

logger = logging.getLogger(__name__)

# Global variables set by main.py
bot = None
db = None
oauth_states = None
CLIENT_ID = None
CLIENT_SECRET = None
REDIRECT_URI = None

def setup_web_module(bot_instance, db_instance, oauth_states_dict, client_id, client_secret, redirect_uri):
    """Initialize web module with dependencies from main.py"""
    global bot, db, oauth_states, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI
    bot = bot_instance
    db = db_instance
    oauth_states = oauth_states_dict
    CLIENT_ID = client_id
    CLIENT_SECRET = client_secret
    REDIRECT_URI = redirect_uri

# --- UI Helper: Common Styles ---
COMMON_STYLE = """
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
    :root { --primary: #007bff; --bg: #f8f9fa; }
    body { background-color: var(--bg); font-family: 'Segoe UI', Tahoma, sans-serif; color: #333; line-height: 1.6; }
    .navbar { background: white; box-shadow: 0 2px 10px rgba(0,0,0,0.05); border-bottom: 2px solid var(--primary); }
    .hero { background: linear-gradient(135deg, #007bff 0%, #0056b3 100%); color: white; padding: 80px 0; border-radius: 0 0 50px 50px; }
    .card-custom { border: none; border-radius: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); background: white; transition: 0.3s; }
    .card-custom:hover { transform: translateY(-5px); }
    footer { padding: 40px 0; background: #212529; color: #aaa; margin-top: 50px; }
    footer a { color: white; text-decoration: none; margin: 0 10px; }
</style>
"""

async def get_nav_html():
    return f"""
    <nav class="navbar navbar-expand-lg navbar-light sticky-top">
        <div class="container">
            <a class="navbar-brand fw-bold text-primary" href="/">☁️ Cloudyte</a>
            <div class="ms-auto">
                <a href="/privacy" class="btn btn-sm btn-outline-primary me-2">Privacy</a>
                <a href="/terms" class="btn btn-sm btn-outline-primary">Terms</a>
            </div>
        </div>
    </nav>
    """

# --- Google API Helpers ---
async def get_user_email(access_token):
    """Get user email using the OAuth2 UserInfo endpoint (Most reliable)"""
    try:
        headers = {'Authorization': f'Bearer {access_token}'}
        async with ClientSession() as session:
            async with session.get('https://www.googleapis.com/oauth2/v3/userinfo', headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('email')
                else:
                    logger.error(f"UserInfo Error: {await response.text()}")
    except Exception as e:
        logger.error(f"Error fetching user email: {e}")
    return None

# --- Route Handlers ---

async def main_page_handler(request):
    """Professional Homepage for Cloudyte Ownership Verification"""
    bot_info = await bot.get_me()
    bot_link = f"https://t.me/{bot_info.username}"
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Cloudyte - Secure Cloud Management</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        {COMMON_STYLE}
    </head>
    <body>
        {await get_nav_html()}
        <div class="hero text-center">
            <div class="container">
                <h1 class="display-4 fw-bold mb-3">Welcome to Cloudyte</h1>
                <p class="lead mb-4">The ultimate security layer for your Google Drive via Telegram.</p>
                <a href="{bot_link}" class="btn btn-light btn-lg px-5 fw-bold text-primary">Open @{bot_info.username}</a>
            </div>
        </div>
        
        <div class="container my-5 text-center">
            <div class="row g-4">
                <div class="col-md-4">
                    <div class="card-custom p-4 h-100">
                        <h4 class="text-primary">Zero-Knowledge</h4>
                        <p>We use AES-256 encryption to ensure only you can access your cloud files.</p>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="card-custom p-4 h-100">
                        <h4 class="text-primary">Secure Auth</h4>
                        <p>Access granted via official Google OAuth 2.0. We never see your password.</p>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="card-custom p-4 h-100">
                        <h4 class="text-primary">Instant Sync</h4>
                        <p>Manage, upload, and encrypt files directly through your Telegram chat.</p>
                    </div>
                </div>
            </div>
        </div>

        <footer class="text-center">
            <div class="container">
                <p>© 2026 <strong>Cloudyte</strong>. Registered to <strong>arshman.me</strong>.</p>
                <div class="mb-3">
                    <a href="/privacy">Privacy Policy</a>
                    <a href="/terms">Terms of Service</a>
                </div>
                <p class="small">Disclaimer: This app is not affiliated with Google LLC.</p>
            </div>
        </footer>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def privacy_policy_handler(request):
    """Professional Privacy Policy for Google Verification"""
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head><title>Privacy Policy - Cloudyte</title>{COMMON_STYLE}</head>
    <body>
        {await get_nav_html()}
        <div class="container my-5">
            <div class="card-custom p-5">
                <h2 class="text-primary mb-4">Privacy Policy</h2>
                <p class="text-muted">Effective Date: April 13, 2026</p>
                <hr>
                <h5>1. Data Collection</h5>
                <p>Cloudyte collects your email address and authentication tokens via Google OAuth to provide cloud management services.</p>
                <h5>2. Google Drive Access</h5>
                <p>We only access your Google Drive files to perform actions you explicitly trigger via the Telegram bot (Upload, Download, Encrypt). We do not store your raw file content on our servers.</p>
                <h5>3. Encryption & Security</h5>
                <p>All data transmitted is protected via TLS 1.3. Your OAuth tokens are encrypted at rest in our database (managed by arshman.me).</p>
                <h5>4. User Control</h5>
                <p>You can revoke access at any time through your Google Account security settings or by deleting your account from the bot.</p>
            </div>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def terms_of_service_handler(request):
    """Professional Terms of Service for Google Verification"""
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head><title>Terms of Service - Cloudyte</title>{COMMON_STYLE}</head>
    <body>
        {await get_nav_html()}
        <div class="container my-5">
            <div class="card-custom p-5">
                <h2 class="text-primary mb-4">Terms of Service</h2>
                <hr>
                <p>By using <strong>Cloudyte</strong>, you agree to the following terms:</p>
                <ul>
                    <li>You will not use the service for any illegal storage or transmission of prohibited content.</li>
                    <li>Cloudyte is a zero-knowledge management tool; we are not responsible for lost encryption keys.</li>
                    <li>We reserve the right to suspend accounts that violate Google Drive's API policies.</li>
                </ul>
            </div>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def oauth_callback_handler(request):
    """Handle OAuth callback from Google with full error reporting"""
    try:
        code = request.query.get('code')
        state = request.query.get('state')
        
        if not code or not state:
            return web.Response(text="Error: Missing code or state parameters.", status=400)
        
        state_data = oauth_states.get(state)
        if not state_data:
            return web.Response(text="Session Expired. Please restart connection from Telegram.", status=400)
        
        user_id = state_data.get('user_id')
        telegram_id = state_data.get('telegram_id')
        
        token_url = "https://oauth2.googleapis.com/token"
        data = {
            'code': code,
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'redirect_uri': REDIRECT_URI,
            'grant_type': 'authorization_code'
        }
        
        async with ClientSession() as session:
            async with session.post(token_url, data=data) as response:
                resp_json = await response.json()
                if response.status != 200:
                    logger.error(f"Google Token Error: {resp_json}")
                    return web.Response(text=f"Auth Error: {resp_json.get('error_description', 'Invalid Request')}", status=400)
                tokens = resp_json
        
        email = await get_user_email(tokens['access_token'])
        if not email:
            return web.Response(text="Failed to retrieve account email.", status=400)
        
        tokens_data = {
            'access_token': tokens['access_token'],
            'refresh_token': tokens.get('refresh_token'),
            'expires_at': time.time() + tokens.get('expires_in', 3600)
        }
        
        await db.add_account(user_id, email, tokens_data)
        
        # Notify user on Telegram
        try: await bot.send_message(telegram_id, f"✅ <b>Cloudyte Linked:</b> {email}", parse_mode="HTML")
        except: pass
        
        return web.Response(text="<html><body style='text-align:center;padding-top:100px;font-family:sans-serif;'><h1 style='color:#007bff;'>Success!</h1><p>Cloudyte is connected. Close this window and return to Telegram.</p></body></html>", content_type='text/html')
        
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return web.Response(text="Internal Server Error", status=500)

def create_web_app():
    """Build the aiohttp web application"""
    app = web.Application()
    app.router.add_get('/', main_page_handler)
    app.router.add_get('/oauth_callback', oauth_callback_handler)
    app.router.add_get('/privacy', privacy_policy_handler)
    app.router.add_get('/terms', terms_of_service_handler)
    return app
