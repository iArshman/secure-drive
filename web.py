"""
Web server module for OAuth callbacks and status page
"""
from aiohttp import web, ClientSession
import logging
import os
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
    """Get user email from Google Drive API (FINAL STABLE VERSION)"""

    try:
        headers = {"Authorization": f"Bearer {access_token}"}

        async with ClientSession() as session:
            async with session.get(
                "https://www.googleapis.com/drive/v3/about?fields=user",
                headers=headers
            ) as response:

                if response.status == 200:
                    data = await response.json()
                    email = data.get("user", {}).get("emailAddress")

                    if not email:
                        logger.error(f"Email missing in Drive response: {data}")

                    return email

                else:
                    error_text = await response.text()
                    logger.error(
                        f"Drive API ERROR {response.status}: {error_text}"
                    )

    except Exception as e:
        logger.error(f"Error getting user email: {e}")

    return None
    
async def main_page_handler(request):
    """Handle main page requests"""
    bot_username = (await bot.get_me()).username if bot else "YOUR_BOT_USERNAME"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Secure Drive</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                text-align: center; 
                padding: 50px; 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                margin: 0;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
            }}
            .container {{ 
                background: white; 
                padding: 40px; 
                border-radius: 16px; 
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                max-width: 500px;
            }}
            .icon {{ font-size: 4em; margin-bottom: 20px; }}
            h1 {{ color: #667eea; margin: 20px 0; }}
            p {{ color: #666; line-height: 1.6; }}
            .status {{ 
                background: #28a745; 
                color: white;
                padding: 10px 20px; 
                border-radius: 20px; 
                display: inline-block;
                margin: 20px 0;
                font-weight: bold;
            }}
            .feature {{
                text-align: left;
                margin: 15px 0;
                padding: 10px;
                background: #f8f9fa;
                border-radius: 8px;
            }}
            .bot-link {{
                display: inline-block;
                background: #667eea;
                color: white;
                padding: 15px 30px;
                border-radius: 8px;
                text-decoration: none;
                margin-top: 20px;
                font-weight: bold;
                transition: background 0.3s;
            }}
            .bot-link:hover {{
                background: #764ba2;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="icon">☁️</div>
            <h1>Secure Drive</h1>
            <div class="status"> Running</div>
            <p>Manage your Google Drive files with Telegram!</p>
            
            <div class="feature">📁 Browse and organize files</div>
            <div class="feature">⬆️ Upload files from Telegram</div>
            <div class="feature">⬇️ Download files to Telegram</div>
            <div class="feature">🔍 Search across your Drive</div>
            <div class="feature">🔗 Generate shareable links</div>
            <div class="feature">💾 View storage information</div>
            
            <a href="https://t.me/{bot_username}" class="bot-link">Open Bot in Telegram</a>
            <p><a href="/privacy" style="color: #667eea; text-decoration: none;">Privacy Policy</a> • 
               <a href="/terms" style="color: #667eea; text-decoration: none;">Terms of Service</a></p>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def oauth_callback_handler(request):
    """Handle OAuth callback from Google with PKCE-safe token exchange"""

    try:
        code = request.query.get("code")
        state = request.query.get("state")

        if not code or not state:
            return web.Response(
                text="Error: Missing code or state parameters.",
                status=400
            )

        # Validate OAuth session state
        state_data = oauth_states.get(state)

        if not state_data:
            return web.Response(
                text="Session expired. Please restart connection from Telegram.",
                status=400
            )

        user_id = state_data.get("user_id")
        telegram_id = state_data.get("telegram_id")
        flow = state_data.get("flow")
        is_backup = state_data.get("is_backup", False)

        if not flow:
            return web.Response(
                text="OAuth flow session missing. Restart connection.",
                status=400
            )

        # Exchange authorization code securely
        # Google may return extra scopes (e.g. openid, userinfo.email) — disable strict check
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
        flow.fetch_token(code=code)

        credentials = flow.credentials

        # Extract tokens
        tokens_data = {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "expires_at": credentials.expiry.timestamp()
        }

        # Get Gmail address
        email = await get_user_email(credentials.token)

        if not email:
            return web.Response(
                text="Failed to retrieve account email.",
                status=400
            )

        # Save account in database
        account_id = await db.add_account(user_id, email, tokens_data)

        # If this was added as a backup account, mark it as such
        if is_backup:
            await db.set_backup_account(user_id, account_id)

        # Remove used OAuth session
        oauth_states.pop(state, None)

        # Notify Telegram user
        try:
            label = "Backup Account" if is_backup else "Secure Drive"
            await bot.send_message(
                telegram_id,
                f"✅ <b>{label} Linked:</b> {email}",
                parse_mode="HTML"
            )
        except Exception:
            pass

        return web.Response(
            text="""
            <html>
            <body style='text-align:center;padding-top:100px;font-family:sans-serif;'>
                <h1 style='color:#007bff;'>Success!</h1>
                <p>Secure Drive is connected. Close this window and return to Telegram.</p>
            </body>
            </html>
            """,
            content_type="text/html"
        )

    except Exception as e:
        logger.error(f"OAuth callback error: {e}")

        return web.Response(
            text="Internal Server Error",
            status=500
        )
        
async def privacy_policy_handler(request):
    """Handle privacy policy page"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Privacy Policy - Secure Drive</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 40px 20px; color: #333; background: #f8f9fa; }
            .container { background: white; padding: 40px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #667eea; border-bottom: 3px solid #667eea; padding-bottom: 15px; margin-bottom: 30px; }
            h2 { color: #495057; margin-top: 30px; margin-bottom: 15px; }
            .last-updated { color: #6c757d; font-style: italic; margin-bottom: 30px; }
            a { color: #667eea; text-decoration: none; }
            .highlight { background: #fff3cd; padding: 15px; border-left: 4px solid #ffc107; margin: 20px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Privacy Policy</h1>
            <p class="last-updated">Last Updated: December 20, 2025</p>
            
            <h2>1. Introduction</h2>
            <p>Secure Drive ("we", "our", or "the bot") provides Google Drive management services through Telegram. This Privacy Policy explains how we collect, use, store, and delete your data.</p>
            
            <h2>2. Information We Collect</h2>
            <p>We collect and process the following information:</p>
            <ul>
                <li><strong>Telegram User ID:</strong> Your unique Telegram identifier to link your account</li>
                <li><strong>Google Drive Email:</strong> The email address associated with your connected Google Drive account</li>
                <li><strong>OAuth Access Tokens:</strong> Encrypted tokens to access your Google Drive on your behalf</li>
                <li><strong>File Metadata:</strong> Temporary file information (names, IDs, types) during operations</li>
            </ul>
            
            <h2>3. How We Use Your Information</h2>
            <p>Your data is used exclusively for:</p>
            <ul>
                <li>Authenticating and managing your Google Drive accounts</li>
                <li>Browsing, uploading, downloading, and searching files</li>
                <li>Creating shareable links</li>
                <li>Performing file operations (rename, delete, organize)</li>
            </ul>
            <p><strong>We do NOT:</strong> Store file contents permanently, share your data with third parties, use your data for advertising, or access your Drive without explicit user commands.</p>
            
            <h2>4. Data Storage and Security</h2>
            <p>We implement industry-standard security measures:</p>
            <ul>
                <li><strong>Encryption:</strong> OAuth tokens are encrypted using AES-256 encryption</li>
                <li><strong>Secure Database:</strong> All data is stored in MongoDB with access controls</li>
                <li><strong>No File Storage:</strong> File contents are never permanently stored on our servers</li>
                <li><strong>Secure Transmission:</strong> All API communications use HTTPS/TLS</li>
            </ul>
            
            <h2>5. Data Retention and Deletion</h2>
            <div class="highlight">
                <strong>Data Retention:</strong>
                <ul>
                    <li><strong>Account Data:</strong> We retain your Telegram User ID, connected email addresses, and OAuth tokens for as long as your Google Drive account remains connected to the bot</li>
                    <li><strong>File Metadata:</strong> Temporary file information is retained only during active operations and is automatically deleted after completion</li>
                    <li><strong>Session Data:</strong> OAuth states and temporary session data expire after 1 hour</li>
                </ul>
                
                <strong>Data Deletion:</strong>
                <p>You have full control over your data. We delete your information in the following ways:</p>
                <ul>
                    <li><strong>Manual Deletion:</strong> Use the /settings command in the bot and select "Remove Account" to immediately delete all stored data for that specific Google Drive account, including OAuth tokens and account information</li>
                    <li><strong>Complete Account Removal:</strong> All your data is permanently deleted from our database within 24 hours of account disconnection</li>
                    <li><strong>Inactive Accounts:</strong> Accounts inactive for more than 12 months may be automatically purged from our system</li>
                    <li><strong>Upon Request:</strong> Contact us at support@arshman.space to request immediate data deletion, and we will comply within 7 business days</li>
                </ul>
                <p><strong>Note:</strong> After deletion, you will need to re-authenticate if you wish to use the service again. Data deletion is irreversible.</p>
            </div>
            
            <h2>6. Google API Services User Data Policy</h2>
            <p>Secure Drive's use and transfer of information received from Google APIs adheres to the <a href="https://developers.google.com/terms/api-services-user-data-policy" target="_blank">Google API Services User Data Policy</a>, including the Limited Use requirements.</p>
            <p>We only request the minimum necessary permissions to provide our service and never use your Google user data for purposes unrelated to providing and improving Secure's features.</p>
            
            <h2>7. Third-Party Services</h2>
            <p>We use the following third-party services:</p>
            <ul>
                <li><strong>Telegram:</strong> For bot messaging interface</li>
                <li><strong>Google Drive API:</strong> For file operations</li>
                <li><strong>MongoDB:</strong> For secure data storage</li>
            </ul>
            <p>Each service has its own privacy policy and data handling practices.</p>
            
            <h2>8. Your Rights</h2>
            <p>You have the right to:</p>
            <ul>
                <li>Access your stored data</li>
                <li>Request data deletion at any time</li>
                <li>Revoke Google Drive access</li>
                <li>Disconnect your account</li>
            </ul>
            
            <h2>9. Changes to This Policy</h2>
            <p>We may update this Privacy Policy from time to time. Continued use of the bot after changes constitutes acceptance of the updated policy.</p>
            
            <h2>10. Contact Us</h2>
            <p>For questions or concerns about this Privacy Policy or your data, contact us at: <a href="mailto:support@arshman.space">support@arshman.space</a></p>
            
            <p style="margin-top: 40px; text-align: center;"><a href="/">← Back to Home</a></p>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def terms_of_service_handler(request):
    """Handle terms of service page"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Terms of Service - Secure Drive</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 40px 20px; color: #333; background: #f8f9fa; }
            .container { background: white; padding: 40px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #667eea; border-bottom: 3px solid #667eea; padding-bottom: 15px; margin-bottom: 30px; }
            h2 { color: #495057; margin-top: 30px; margin-bottom: 15px; }
            .last-updated { color: #6c757d; font-style: italic; margin-bottom: 30px; }
            a { color: #667eea; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Terms of Service</h1>
            <p class="last-updated">Last Updated: December 12, 2025</p>
            
            <h2>1. Acceptance of Terms</h2>
            <p>By using Secure Drive, you agree to these Terms of Service.</p>
            
            <h2>2. Description of Service</h2>
            <p>Secure Drive allows managing Google Drive files through Telegram, including browsing, uploading, downloading, searching, and sharing.</p>
            
            <h2>3. User Responsibilities</h2>
            <p>Use the service lawfully, keep your account secure, and respect others' data.</p>
            
            <h2>4. Drive Account Access</h2>
            <p>By connecting, you grant Secure drive permission to read, create, modify, and delete files in your Drive on your behalf.</p>
            
            <h2>5. Data Privacy</h2>
            <p>See our <a href="/privacy">Privacy Policy</a> for details on data handling.</p>
            
            <p style="margin-top: 40px; text-align: center;"><a href="/">← Back to Home</a></p>
        </div>
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
