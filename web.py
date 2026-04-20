"""
Internal OAuth Bridge — web.py
Serves on the same port as the bot's token receiver.

Routes:
  GET  /               — UI with generator + docs
  GET  /start-auth     — Redirects user to Google sign-in
  GET  /oauth_callback — Google redirects here after sign-in
  POST /tokens         — Receives tokens from external OAuth bridge (external mode)

Injected by main.py via setup():
  bot, db, BOT_SERVER_URL, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, SCOPES, PORT, OAUTH_MODE
"""

import os
import json
import base64
import logging
import time
from urllib.parse import urlencode
from aiohttp import web, ClientSession

logger = logging.getLogger(__name__)

# Injected by main.py
bot           = None
db            = None
CLIENT_ID     = None
CLIENT_SECRET = None
REDIRECT_URI  = None
SCOPES        = None
PORT          = 1025
BOT_SERVER_URL = ""
OAUTH_MODE    = "internal"

def setup(bot_inst, db_inst, bot_url, client_id, client_secret,
          redirect_uri, scopes, port, oauth_mode):
    global bot, db, BOT_SERVER_URL, CLIENT_ID, CLIENT_SECRET
    global REDIRECT_URI, SCOPES, PORT, OAUTH_MODE
    bot            = bot_inst
    db             = db_inst
    BOT_SERVER_URL = bot_url
    CLIENT_ID      = client_id
    CLIENT_SECRET  = client_secret
    REDIRECT_URI   = redirect_uri
    SCOPES         = scopes
    PORT           = port
    OAUTH_MODE     = oauth_mode

# Helpers

def _escape(text):
    import html
    return html.escape(str(text))

async def _get_email(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    async with ClientSession() as s:
        async with s.get(
            "https://www.googleapis.com/drive/v3/about?fields=user",
            headers=headers
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data.get("user", {}).get("emailAddress")
    return None

def _pack_state(internal_user_id, telegram_id, is_backup):
    packed_u = f"{internal_user_id}:{telegram_id}:{1 if is_backup else 0}"
    state = {"u": packed_u, "r": f"{BOT_SERVER_URL}/tokens"}
    return base64.urlsafe_b64encode(json.dumps(state).encode()).decode().rstrip("=")

def _unpack_u(packed_u):
    parts = packed_u.split(":")
    return int(parts[0]), int(parts[1]), bool(int(parts[2]))

async def _save_and_notify(internal_user_id, telegram_id, is_backup, email, creds):
    tokens_data = {
        "access_token":  creds["access_token"],
        "refresh_token": creds.get("refresh_token"),
        "expires_at":    creds.get("expires_at"),
    }
    account_id = await db.add_account(internal_user_id, email, tokens_data)
    if is_backup:
        await db.set_backup_account(internal_user_id, account_id)
        label = "Backup account"
    else:
        label = "Account"
    logger.info(f"Saved tokens: user={internal_user_id} tg={telegram_id} email={email} backup={is_backup}")
    try:
        await bot.send_message(
            telegram_id,
            f"<b>{label} linked:</b> <code>{_escape(email)}</code>\n\nUse /files to start browsing.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Telegram notify failed for {telegram_id}: {e}")

# Route: GET /

async def home_handler(request):
    mode_badge = (
        '<span style="background:#16a34a;color:white;padding:4px 12px;border-radius:20px;font-size:.85em">Internal Mode</span>'
        if OAUTH_MODE == "internal" else
        '<span style="background:#2563eb;color:white;padding:4px 12px;border-radius:20px;font-size:.85em">External Mode</span>'
    )
    callback_url = REDIRECT_URI or f"http://YOUR_IP:{PORT}/oauth_callback"
    html_page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Secure Drive OAuth</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;color:#333;padding:30px 16px}}
.wrap{{max-width:860px;margin:auto}}
.card{{background:white;padding:28px 32px;border-radius:14px;box-shadow:0 2px 12px rgba(0,0,0,.07);margin-bottom:24px}}
h1{{color:#1a73e8;font-size:1.7em;margin-bottom:6px}}
h2{{color:#1a73e8;font-size:1.15em;margin-bottom:14px}}
h3{{color:#374151;font-size:1em;margin:16px 0 6px}}
p{{color:#555;line-height:1.65;margin-bottom:10px}}
code{{background:#f1f3f4;padding:2px 7px;border-radius:5px;font-family:monospace;color:#c0392b;font-size:.92em}}
pre{{background:#1e1e2e;color:#89b4fa;padding:16px;border-radius:10px;overflow-x:auto;font-size:.88em;line-height:1.5;margin:10px 0}}
.step{{border-left:4px solid #1a73e8;padding-left:16px;margin-bottom:18px}}
input,select{{width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:.95em;margin:6px 0 12px}}
input:focus,select:focus{{border-color:#1a73e8;outline:none}}
.btn{{background:#1a73e8;color:white;border:none;padding:11px 26px;border-radius:8px;font-size:.95em;font-weight:600;cursor:pointer}}
.btn:hover{{background:#1558b0}}
.notice{{background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af;padding:14px 18px;border-radius:10px;font-size:.9em;margin-bottom:16px}}
</style></head><body>
<div class="wrap">
  <div class="card">
    <h1>Secure Drive OAuth</h1>
    <p style="margin-top:8px">{mode_badge}</p>
    <p style="margin-top:14px">Handles Google OAuth for the Secure Drive Telegram bot.</p>
  </div>

  <div class="card">
    <h2>Google Console Setup</h2>
    <div class="notice">Add this as an <strong>Authorized Redirect URI</strong> in Google Cloud Console:</div>
    <pre>{callback_url}</pre>
    <p>Google Cloud Console → APIs &amp; Services → Credentials → OAuth Client → Authorized redirect URIs</p>
  </div>

  <div class="card">
    <h2>Manual Token Generator</h2>
    <label>Internal User ID</label>
    <input type="text" id="uid" placeholder="e.g. 390607644363272">
    <label>Telegram ID</label>
    <input type="text" id="tid" placeholder="e.g. 7725409374">
    <label>Account Type</label>
    <select id="backup">
      <option value="0">Primary Account</option>
      <option value="1">Backup Account</option>
    </select>
    <button class="btn" onclick="startAuth()">Sign in with Google</button>
  </div>

  <div class="card">
    <h2>Integration Guide</h2>
    <div class="step">
      <h3>State format (base64 encoded)</h3>
      <pre>{{ "u": "internalId:telegramId:isBackup", "r": "http://BOT_IP:{PORT}/tokens" }}</pre>
    </div>
    <div class="step">
      <h3>Start OAuth</h3>
      <pre>GET /start-auth?state=BASE64_STATE</pre>
    </div>
    <div class="step">
      <h3>Modes</h3>
      <p><strong>Internal:</strong> tokens saved to MongoDB directly on this server.</p>
      <p><strong>External:</strong> tokens POSTed to <code>{BOT_SERVER_URL}/tokens</code>.</p>
    </div>
    <div class="step">
      <h3>.env config</h3>
      <pre>OAUTH_MODE=internal        # internal | external
OAUTH_SERVICE_URL=https://oauth.arshman.me  # external mode only
BOT_SERVER_URL=http://YOUR_IP:{PORT}        # auto-detected if blank</pre>
    </div>
  </div>
</div>
<script>
function startAuth() {{
  var uid = document.getElementById('uid').value.trim();
  var tid = document.getElementById('tid').value.trim();
  var bak = document.getElementById('backup').value;
  if (!uid || !tid) {{ alert('Enter both IDs'); return; }}
  var state = btoa(JSON.stringify({{u: uid+':'+tid+':'+bak, r:'manual'}})).replace(/=/g,'');
  window.location.href = '/start-auth?state=' + state;
}}
</script>
</body></html>"""
    return web.Response(text=html_page, content_type="text/html")

# Route: GET /start-auth

async def start_auth_handler(request):
    state_raw = request.query.get("state")
    if not state_raw:
        return web.Response(text="Missing state parameter.", status=400)
    scope_str = " ".join(SCOPES) if isinstance(SCOPES, list) else SCOPES
    params = {
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "scope":         scope_str,
        "access_type":   "offline",
        "prompt":        "consent",
        "state":         state_raw,
    }
    return web.HTTPFound(f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}")

# Route: GET /oauth_callback

async def oauth_callback_handler(request):
    code      = request.query.get("code")
    state_str = request.query.get("state")
    if not code or not state_str:
        return web.Response(text="Missing code or state.", status=400)
    try:
        async with ClientSession() as session:
            async with session.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code":          code,
                    "client_id":     CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "redirect_uri":  REDIRECT_URI,
                    "grant_type":    "authorization_code",
                }
            ) as resp:
                token_data = await resp.json()
                if resp.status != 200:
                    logger.error(f"Token exchange failed: {token_data}")
                    return web.Response(
                        text=f"Token exchange failed: {token_data.get('error_description', token_data.get('error'))}",
                        status=400
                    )

        access_token  = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_at    = time.time() + token_data.get("expires_in", 3600)
        email         = await _get_email(access_token)

        padding    = "=" * (4 - len(state_str) % 4)
        state_data = json.loads(base64.urlsafe_b64decode(state_str + padding).decode())
        packed_u   = state_data.get("u", "")
        return_url = state_data.get("r", "manual")

        creds = {
            "access_token":  access_token,
            "refresh_token": refresh_token,
            "expires_at":    expires_at,
        }

        # Manual mode — show tokens on screen
        if return_url == "manual":
            display = json.dumps({"status":"success","user_id":packed_u,"email":email,"credentials":creds}, indent=4)
            return web.Response(
                text=f"""<html><body style="font-family:sans-serif;padding:40px;background:#f0f2f5">
                <div style="background:white;padding:30px;border-radius:12px;max-width:700px;margin:auto">
                <h2 style="color:#1a73e8">Tokens Generated</h2>
                <p>Account: <b>{_escape(email)}</b></p>
                <pre style="background:#1e1e2e;color:#89b4fa;padding:20px;border-radius:10px;overflow-x:auto">{display}</pre>
                <p><a href="/" style="color:#1a73e8">Back</a></p>
                </div></body></html>""",
                content_type="text/html"
            )

        # Internal mode — save directly
        if OAUTH_MODE == "internal":
            internal_user_id, telegram_id, is_backup = _unpack_u(packed_u)
            await _save_and_notify(internal_user_id, telegram_id, is_backup, email, creds)

        # External mode — POST to bot
        else:
            payload = {"status":"success","user_id":packed_u,"email":email,"credentials":creds}
            async with ClientSession() as session:
                async with session.post(return_url, json=payload, timeout=20) as r:
                    if r.status != 200:
                        err = await r.text()
                        return web.Response(text=f"Bot server rejected tokens: {err}", status=500)

        return web.Response(
            text=f"""<html><body style="text-align:center;padding-top:80px;font-family:sans-serif;background:#f0f2f5">
            <div style="background:white;padding:40px;border-radius:14px;max-width:420px;margin:auto;box-shadow:0 4px 12px rgba(0,0,0,.1)">
            <div style="font-size:3em">&#x2705;</div>
            <h2 style="color:#16a34a;margin:14px 0 8px">Authorized!</h2>
            <p style="color:#555"><b>{_escape(email)}</b> linked.</p>
            <p style="color:#888;margin-top:12px;font-size:.9em">Close this window and return to Telegram.</p>
            </div></body></html>""",
            content_type="text/html"
        )
    except Exception as e:
        logger.error(f"oauth_callback error: {e}", exc_info=True)
        return web.Response(text=f"Internal error: {e}", status=500)

# Route: POST /tokens (external mode receiver on bot side)

async def tokens_handler(request):
    try:
        payload = await request.json()
        logger.info(f"POST /tokens: {payload}")
        if payload.get("status") != "success":
            return web.Response(text="ignored", status=200)
        packed_u = payload.get("user_id", "")
        email    = payload.get("email")
        creds    = payload.get("credentials", {})
        try:
            internal_user_id, telegram_id, is_backup = _unpack_u(packed_u)
        except Exception:
            logger.error(f"Bad user_id: {packed_u!r}")
            return web.Response(text="bad user_id format", status=400)
        if not email or not creds.get("access_token"):
            return web.Response(text="missing email or tokens", status=400)
        await _save_and_notify(internal_user_id, telegram_id, is_backup, email, creds)
        return web.Response(text="ok", status=200)
    except Exception as e:
        logger.error(f"POST /tokens error: {e}", exc_info=True)
        return web.Response(text="error", status=500)

# App factory

def create_app():
    app = web.Application()
    app.router.add_get( "/",                home_handler)
    app.router.add_get( "/start-auth",      start_auth_handler)
    app.router.add_get( "/oauth_callback",  oauth_callback_handler)
    app.router.add_get( "/oauth_callback/", oauth_callback_handler)
    app.router.add_post("/tokens",          tokens_handler)
    return app
