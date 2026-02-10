"""
Web server module for OAuth callbacks and status page
"""
from aiohttp import web, ClientSession
import logging
import time

logger = logging.getLogger(__name__)

# Will be set by main.py
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

async def get_user_email(access_token):
    """Get user email from Google Drive API"""
    try:
        headers = {'Authorization': f'Bearer {access_token}'}
        async with ClientSession() as session:
            async with session.get('https://www.googleapis.com/drive/v3/about?fields=user', headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('user', {}).get('emailAddress')
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to get user info. Status: {response.status}, Response: {error_text}")
    except Exception as e:
        logger.error(f"Error getting user email: {e}")
    return None

async def main_page_handler(request):
    """Handle main page requests"""
    bot_username = (await bot.get_me()).username if bot else "YOUR_BOT_USERNAME"
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Secure Drive Service</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            :root {{
                --primary-color: #0056b3;
                --text-color: #24292e;
                --bg-color: #f6f8fa;
                --card-bg: #ffffff;
                --border-color: #d1d5da;
                --secondary-text: #586069;
            }}
            body {{ 
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background-color: var(--bg-color);
                color: var(--text-color);
                margin: 0;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                line-height: 1.5;
            }}
            .container {{ 
                background: var(--card-bg); 
                padding: 40px; 
                border: 1px solid var(--border-color);
                border-radius: 6px; 
                width: 100%;
                max-width: 450px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.04);
            }}
            .header-bar {{
                margin-bottom: 24px;
                padding-bottom: 24px;
                border-bottom: 1px solid var(--border-color);
            }}
            h1 {{ 
                font-size: 20px; 
                font-weight: 600; 
                margin: 0;
                color: var(--text-color);
            }}
            .status-indicator {{
                display: flex;
                align-items: center;
                font-size: 14px;
                color: #2da44e;
                margin-bottom: 8px;
            }}
            .status-dot {{
                width: 8px;
                height: 8px;
                background-color: #2da44e;
                border-radius: 50%;
                margin-right: 8px;
            }}
            p {{
                margin-bottom: 16px;
                color: var(--secondary-text);
                font-size: 14px;
            }}
            .features-list {{
                margin: 24px 0;
            }}
            .feature-row {{
                display: flex;
                justify-content: space-between;
                padding: 8px 0;
                font-size: 14px;
                border-bottom: 1px solid #eaecef;
            }}
            .feature-row:last-child {{ border-bottom: none; }}
            .feature-label {{ color: var(--secondary-text); }}
            .feature-value {{ font-weight: 500; }}
            
            .btn {{
                display: block;
                width: 100%;
                padding: 10px 0;
                background-color: var(--primary-color);
                color: white;
                text-decoration: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
                text-align: center;
                transition: opacity 0.2s;
                box-sizing: border-box;
                margin-top: 24px;
            }}
            .btn:hover {{ opacity: 0.9; }}
            
            .footer {{
                margin-top: 24px;
                font-size: 12px;
                color: var(--secondary-text);
                text-align: center;
            }}
            .footer a {{
                color: var(--secondary-text);
                text-decoration: none;
                margin: 0 8px;
            }}
            .footer a:hover {{ text-decoration: underline; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header-bar">
                <div class="status-indicator">
                    <div class="status-dot"></div>
                    <span>System Operational</span>
                </div>
                <h1>Secure Drive Integration</h1>
            </div>
            
            <p>End-to-end encrypted storage management interface.</p>
            
            <div class="features-list">
                <div class="feature-row">
                    <span class="feature-label">Encryption Protocol</span>
                    <span class="feature-value">AES-256</span>
                </div>
                <div class="feature-row">
                    <span class="feature-label">Architecture</span>
                    <span class="feature-value">Zero-Knowledge</span>
                </div>
                <div class="feature-row">
                    <span class="feature-label">Storage Provider</span>
                    <span class="feature-value">Google Drive API</span>
                </div>
            </div>
            
            <a href="https://t.me/{bot_username}" class="btn">Launch Interface</a>
            
            <div class="footer">
                <a href="/privacy">Privacy Policy</a>
                <a href="/terms">Terms of Service</a>
            </div>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def oauth_callback_handler(request):
    """Handle OAuth callback from Google"""
    try:
        code = request.query.get('code')
        state = request.query.get('state')
        
        if not code or not state:
            return web.Response(text="Bad Request: Missing parameters", status=400)
        
        try:
            user_id = int(state.split('_')[0])
        except (ValueError, IndexError):
            return web.Response(text="Bad Request: Invalid state", status=400)
        
        # Exchange code for tokens
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
                if response.status != 200:
                    return web.Response(text="Authentication Failed. Please try again.", status=400)
                
                tokens = await response.json()
        
        # Get user email
        email = await get_user_email(tokens['access_token'])
        if not email:
            return web.Response(text="Could not retrieve user profile.", status=400)
        
        tokens_data = {
            'access_token': tokens['access_token'],
            'refresh_token': tokens.get('refresh_token'),
            'expires_at': time.time() + tokens.get('expires_in', 3600)
        }
        
        await db.add_account(user_id, email, tokens_data)
        
        await bot.send_message(
            user_id,
            f"Account Connected: {email}\nSecure encryption protocols active."
        )
        
        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>Connection Successful</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                    background-color: #f6f8fa;
                    color: #24292e;
                    margin: 0;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                }}
                .container {{ 
                    background: white; 
                    padding: 40px; 
                    border: 1px solid #d1d5da;
                    border-radius: 6px; 
                    width: 100%;
                    max-width: 450px;
                    text-align: center;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
                }}
                h1 {{ 
                    font-size: 20px; 
                    color: #2da44e; 
                    margin-bottom: 16px; 
                    font-weight: 600;
                }}
                .email-box {{
                    background: #f6f8fa;
                    padding: 12px;
                    border: 1px solid #eaecef;
                    border-radius: 4px;
                    font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
                    font-size: 13px;
                    margin: 24px 0;
                    color: #24292e;
                    word-break: break-all;
                }}
                p {{ color: #586069; font-size: 14px; margin: 0; }}
                .info {{ font-size: 12px; color: #6a737d; margin-top: 32px; border-top: 1px solid #eaecef; padding-top: 16px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Connection Established</h1>
                <p>The following account has been successfully linked:</p>
                <div class="email-box">{email}</div>
                <p>You may now close this window and return to the application.</p>
                <div class="info">Secure connection active via TLS 1.3</div>
            </div>
        </body>
        </html>
        """
        
        return web.Response(text=html, content_type='text/html')
        
    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        return web.Response(text="Internal Server Error", status=500)

async def privacy_policy_handler(request):
    """Handle privacy policy page"""
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>Privacy Policy</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 40px 20px; color: #24292e; background-color: #ffffff; }
            h1 { border-bottom: 1px solid #eaecef; padding-bottom: 10px; margin-bottom: 24px; font-size: 24px; font-weight: 600; }
            h2 { margin-top: 32px; font-size: 18px; font-weight: 600; border-bottom: 1px solid #eaecef; padding-bottom: 6px; }
            p, ul { margin-bottom: 16px; color: #444; font-size: 14px; }
            .meta { color: #6a737d; font-size: 13px; margin-bottom: 32px; }
            .section { margin-bottom: 24px; }
        </style>
    </head>
    <body>
        <h1>Privacy Policy</h1>
        <div class="meta">Effective Date: February 10, 2026</div>
        
        <div class="section">
            <h2>1. Data Collection</h2>
            <p>We collect minimal data required for service operation:</p>
            <ul>
                <li>User Identifier (Telegram ID)</li>
                <li>Account Identifier (Google Email)</li>
                <li>Authentication Tokens (OAuth 2.0)</li>
            </ul>
        </div>
        
        <div class="section">
            <h2>2. Encryption Standards</h2>
            <p>This service employs AES-256 encryption for data at rest and TLS for data in transit. File contents and filenames are encrypted client-side before transmission to Google Drive servers.</p>
        </div>
        
        <div class="section">
            <h2>3. Data Usage</h2>
            <p>Data is utilized strictly for authentication and file management operations requested by the user. No data is shared with third parties or used for analytics.</p>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def terms_of_service_handler(request):
    """Handle terms of service page"""
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>Terms of Service</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 40px 20px; color: #24292e; background-color: #ffffff; }
            h1 { border-bottom: 1px solid #eaecef; padding-bottom: 10px; margin-bottom: 24px; font-size: 24px; font-weight: 600; }
            h2 { margin-top: 32px; font-size: 18px; font-weight: 600; }
            p { margin-bottom: 16px; color: #444; font-size: 14px; }
        </style>
    </head>
    <body>
        <h1>Terms of Service</h1>
        
        <h2>1. Service Scope</h2>
        <p>This application provides an encryption layer for cloud storage services. By using this service, you acknowledge that you maintain responsibility for your encryption keys.</p>
        
        <h2>2. Data Liability</h2>
        <p>The service provider is not liable for data loss resulting from lost encryption keys, API outages, or user negligence.</p>
        
        <h2>3. Usage Restrictions</h2>
        <p>Users must comply with Google Drive Terms of Service and Telegram Terms of Service. Illegal activities are strictly prohibited.</p>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

def create_web_app():
    """Create and configure the web application"""
    app = web.Application()
    app.router.add_get('/', main_page_handler)
    app.router.add_get('/oauth_callback', oauth_callback_handler)
    app.router.add_get('/privacy', privacy_policy_handler)
    app.router.add_get('/terms', terms_of_service_handler)
    return app
