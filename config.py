"""
Configuration settings for WOWDrive Bot
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

# Google Drive API configuration
GOOGLE_CREDENTIALS_FILE = 'client_secrets.json'
GOOGLE_TOKEN_FILE = 'token.json'
SCOPES = ['https://www.googleapis.com/auth/drive.file']

# Upload configuration
UPLOAD_FOLDER = 'uploads'
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
CHUNK_SIZE = 1024 * 1024  # 1MB chunks
PROGRESS_UPDATE_INTERVAL = 20  # seconds

# Rate limiting
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_PERIOD = 60  # seconds

# Logging configuration
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


# Personal hosting manager configuration
OWNER_USER_ID = os.getenv('OWNER_USER_ID')
PROJECT_REGISTRY_FILE = os.getenv('PROJECT_REGISTRY_FILE', 'data/projects.json')
RUNTIME_STATE_FILE = os.getenv('RUNTIME_STATE_FILE', 'data/runtime.json')
PROJECT_WORKSPACE = os.getenv('PROJECT_WORKSPACE', 'data/workspace')
PROJECT_LOG_DIR = os.getenv('PROJECT_LOG_DIR', 'data/logs')
RUNTIME_STATE_DRIVE_NAME = os.getenv('RUNTIME_STATE_DRIVE_NAME', 'runtime.json')
MAX_RUNNING_PROJECTS = int(os.getenv('MAX_RUNNING_PROJECTS', '3'))
AUTO_RESTART_DELAY_SECONDS = int(os.getenv('AUTO_RESTART_DELAY_SECONDS', '5'))
MAX_AUTO_RESTART_ATTEMPTS = int(os.getenv('MAX_AUTO_RESTART_ATTEMPTS', '5'))
PROJECT_REGISTRY_DRIVE_NAME = os.getenv('PROJECT_REGISTRY_DRIVE_NAME', 'projects.json')
MONGODB_URL = os.getenv('MONGODB_URL') or os.getenv('MONGODB_URI') or os.getenv('MONGOBD_IRL')
MONGODB_DATABASE = os.getenv('MONGODB_DATABASE', 'telegram_hosting_bot')
OWNER_CHAT_ID = os.getenv('OWNER_CHAT_ID')
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
GOOGLE_REFRESH_TOKEN = os.getenv('GOOGLE_REFRESH_TOKEN')
LOG_ARCHIVE_DRIVE_PREFIX = os.getenv('LOG_ARCHIVE_DRIVE_PREFIX', 'logs')
MAX_PROJECT_VERSIONS = int(os.getenv('MAX_PROJECT_VERSIONS', '5'))
