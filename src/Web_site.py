#!/usr/bin/env python3
"""
Web server for Google OAuth authentication
"""

import os
import json
import logging
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from config import *

# Configure logging
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Railway and similar platforms run Flask behind a proxy. ProxyFix makes
# request.url use the original HTTPS scheme sent by the public domain.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')

# OAuth configuration
SCOPES = ['https://www.googleapis.com/auth/drive.file']
CLIENT_SECRETS_FILE = 'client_secrets.json'

# Get redirect URI from environment or derive it from the public Railway URL.
WEB_URL = os.getenv('WEB_URL', '').rstrip('/')
REDIRECT_URI = os.getenv(
    'REDIRECT_URI',
    f"{WEB_URL}/callback" if WEB_URL else 'http://localhost:8080/callback'
)

# Store user credentials (in production, use a database)
user_credentials = {}


def get_flow():
    """Get OAuth flow configuration from environment when available."""
    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        client_config = {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        return Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
            autogenerate_code_verifier=False,
        )
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        autogenerate_code_verifier=False,
    )
    return flow


def user_id_from_state(state_value: str) -> str:
    """Extract Telegram user id from OAuth state.

    /auth/<user_id> stores the Telegram id inside state as tg:<id>.
    Older links may still return a random Google state, so keep a safe fallback.
    """
    if state_value and state_value.startswith('tg:'):
        return state_value[3:]
    return session.get('telegram_user_id') or state_value or 'default'


def save_credentials_for_user(user_id: str, credentials: Credentials) -> None:
    """Persist credentials in the format used by drive.py."""
    token_file = f"token_{user_id}.json"
    with open(token_file, 'w') as f:
        f.write(credentials.to_json())


@app.route('/')
def index():
    """Main page"""
    return render_template('Index.html')


@app.route('/login')
def login():
    """Initiate Google OAuth login"""
    try:
        flow = get_flow()
        state_value = f"web:{os.urandom(16).hex()}"
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent',
            state=state_value
        )
        session['state'] = state_value
        return redirect(authorization_url)
    except Exception as e:
        logger.error(f"Login error: {e}")
        return render_template('notfound.html', error=str(e))


@app.route('/callback')
def callback():
    """Handle OAuth callback"""
    try:
        flow = get_flow()
        authorization_response = request.url
        if os.getenv('FORCE_HTTPS', 'true').lower() == 'true':
            authorization_response = authorization_response.replace('http://', 'https://', 1)
        flow.fetch_token(authorization_response=authorization_response)

        credentials = flow.credentials
        user_id = user_id_from_state(request.args.get('state'))

        # Store credentials in memory and on disk so bot.get_drive_manager(user_id)
        # can load token_<telegram_user_id>.json after /login completes.
        user_credentials[user_id] = credentials
        save_credentials_for_user(user_id, credentials)

        logger.info("OAuth completed for user %s; credentials saved to token_%s.json", user_id, user_id)

        return render_template('googlesignIn.html',
                               success=True,
                               user_id=user_id)
    except Exception as e:
        logger.error(f"Callback error: {e}")
        return render_template('googlesignIn.html',
                               success=False,
                               error=str(e))


@app.route('/auth/<user_id>')
def auth_user(user_id):
    """Get auth URL for specific user"""
    try:
        flow = get_flow()
        state_value = f"tg:{user_id}"
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent',
            state=state_value
        )
        session['state'] = state_value
        session['telegram_user_id'] = user_id
        return jsonify({
            'success': True,
            'auth_url': authorization_url
        })
    except Exception as e:
        logger.error(f"Auth URL error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        })


@app.route('/credentials/<user_id>')
def get_credentials(user_id):
    """Get user credentials"""
    if user_id in user_credentials:
        return jsonify({
            'success': True,
            'credentials': user_credentials[user_id].to_json()
        })
    else:
        # Try to load from file
        token_file = f"token_{user_id}.json"
        if os.path.exists(token_file):
            try:
                with open(token_file, 'r') as f:
                    creds_data = json.load(f)
                credentials = Credentials.from_authorized_user_info(creds_data)

                # Refresh if needed
                if credentials.expired and credentials.refresh_token:
                    credentials.refresh(Request())
                    user_credentials[user_id] = credentials
                    with open(token_file, 'w') as f:
                        f.write(credentials.to_json())

                return jsonify({
                    'success': True,
                    'credentials': credentials.to_json()
                })
            except Exception as e:
                logger.error(f"Error loading credentials: {e}")
                return jsonify({
                    'success': False,
                    'error': str(e)
                })
        else:
            return jsonify({
                'success': False,
                'error': 'No credentials found'
            })


@app.route('/revoke/<user_id>')
def revoke_credentials(user_id):
    """Revoke user credentials"""
    try:
        if user_id in user_credentials:
            credentials = user_credentials[user_id]
            credentials.revoke(Request())
            del user_credentials[user_id]

        # Remove token file
        token_file = f"token_{user_id}.json"
        if os.path.exists(token_file):
            os.remove(token_file)

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Revoke error: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/policy')
def policy():
    """Privacy policy page"""
    return render_template('policy.html')


@app.route('/terms')
def terms():
    """Terms of service page"""
    return render_template('terms.html')


@app.errorhandler(404)
def not_found(error):
    """404 error handler"""
    return render_template('notfound.html'), 404


def start_web_server():
    """Start the web server"""
    port = int(os.getenv('PORT', 8080))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
