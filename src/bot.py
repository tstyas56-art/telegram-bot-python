#!/usr/bin/env python3
"""
WOWDrive Bot - Upload files from Telegram to Google Drive
"""

import asyncio
import logging
import os
import tempfile
import shutil
import uuid
import zipfile
import aiohttp
from datetime import datetime
from typing import Dict, Optional, List
from dataclasses import dataclass
from pathlib import Path

import aiofiles
from asyncio_throttle import Throttler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from telegram.constants import ParseMode

from config import *
from drive import GoogleDriveManager
from logs.project_logs import ProjectLogStore
from models import ProjectRecord, ProjectVersion, utc_now_iso
from project_manager.discovery import discover_project, safe_extract_zip
from project_manager.manager import ProjectManager
from runtime.state import RuntimeStateStore
from storage.project_registry import ProjectRegistry

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class UploadTask:
    user_id: int
    file_id: str
    file_name: str
    file_size: int
    message_id: int
    status: str = "queued"
    progress: int = 0
    drive_file_id: Optional[str] = None
    error: Optional[str] = None


class WOWDriveBot:
    def __init__(self):
        self.upload_queue: List[UploadTask] = []
        self.active_uploads: Dict[int, UploadTask] = {}
        self.user_drive_managers: Dict[int, GoogleDriveManager] = {}
        self.throttler = Throttler(
            rate_limit=RATE_LIMIT_REQUESTS, period=RATE_LIMIT_PERIOD)

        # Ensure upload and hosting folders exist
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        self.registry = ProjectRegistry(PROJECT_REGISTRY_FILE)
        self.runtime_store = RuntimeStateStore(RUNTIME_STATE_FILE, RUNTIME_STATE_DRIVE_NAME)
        self.log_store = ProjectLogStore(PROJECT_LOG_DIR)
        self.project_manager = ProjectManager(
            self.registry, self.runtime_store, PROJECT_WORKSPACE, self.log_store
        )

    def get_drive_manager(self, user_id: int) -> GoogleDriveManager:
        """Get or create Drive manager for user"""
        if user_id not in self.user_drive_managers:
            manager = GoogleDriveManager()
            manager.load_credentials_from_file(str(user_id))
            self.user_drive_managers[user_id] = manager
        return self.user_drive_managers[user_id]

    async def get_auth_url(self, user_id: int) -> Optional[str]:
        """Get authentication URL for user"""
        try:
            # Get the web server URL from environment or use localhost
            web_url = os.getenv('WEB_URL', 'http://localhost:8080')
            async with aiohttp.ClientSession() as session:
                url = f"{web_url}/auth/{user_id}"
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('success'):
                            return data.get('auth_url')
        except Exception as e:
            logger.error(f"Failed to get auth URL: {e}")
        return None

    async def check_credentials(self, user_id: int) -> bool:
        """Check if user has valid credentials"""
        manager = self.get_drive_manager(user_id)
        return manager.service is not None

    async def get_storage_info(self, user_id: int) -> Optional[Dict]:
        """Get user's Google Drive storage information"""
        manager = self.get_drive_manager(user_id)
        return manager.get_storage_info()

    async def list_recent_files(self, user_id: int, limit: int = 10) -> List[Dict]:
        """List recent files from user's Google Drive"""
        manager = self.get_drive_manager(user_id)
        return manager.list_files(limit)

    async def upload_file_chunked(self, task: UploadTask, file_path: str) -> bool:
        """Upload large file using chunked upload with progress tracking"""
        manager = self.get_drive_manager(task.user_id)
        if not manager.service:
            task.error = "Authentication required. Please use /login first."
            return False

        def progress_callback(progress: int):
            task.progress = progress
            asyncio.create_task(self.update_progress_message(task))

        try:
            task.status = "uploading"
            drive_file_id = manager.upload_file_chunked(
                file_path,
                task.file_name,
                progress_callback
            )

            if drive_file_id:
                task.drive_file_id = drive_file_id
                task.status = "completed"
                task.progress = 100
                await self.update_progress_message(task)
                return True
            else:
                task.error = "Upload failed"
                task.status = "failed"
                await self.update_progress_message(task)
                return False

        except Exception as e:
            logger.error(f"Upload failed for task {task.file_id}: {e}")
            task.error = str(e)
            task.status = "failed"
            await self.update_progress_message(task)
            return False

    async def upload_file_direct(self, task: UploadTask, file_path: str) -> bool:
        """Upload small file directly"""
        manager = self.get_drive_manager(task.user_id)
        if not manager.service:
            task.error = "Authentication required. Please use /login first."
            return False

        try:
            task.status = "uploading"
            drive_file_id = manager.upload_file(file_path, task.file_name)

            if drive_file_id:
                task.drive_file_id = drive_file_id
                task.status = "completed"
                task.progress = 100
                await self.update_progress_message(task)
                return True
            else:
                task.error = "Upload failed"
                task.status = "failed"
                await self.update_progress_message(task)
                return False

        except Exception as e:
            logger.error(f"Direct upload failed for task {task.file_id}: {e}")
            task.error = str(e)
            task.status = "failed"
            await self.update_progress_message(task)
            return False

    async def update_progress_message(self, task: UploadTask):
        """Update the progress message for an upload task"""
        try:
            if task.status == "queued":
                text = f"📤 **{task.file_name}**\n\n⏳ Request added to the queue!"
            elif task.status == "uploading":
                text = f"📤 **{task.file_name}**\n\n🔄 Uploading... {task.progress}%"
            elif task.status == "completed":
                text = f"✅ **{task.file_name}**\n\n🎉 Upload completed!\n\n🔗 File ID: `{task.drive_file_id}`"
            elif task.status == "failed":
                text = f"❌ **{task.file_name}**\n\n💥 Upload failed!\n\nError: {task.error}"
            else:
                text = f"📤 **{task.file_name}**\n\nStatus: {task.status}"

            keyboard = []
            if task.status == "uploading":
                keyboard.append([InlineKeyboardButton(
                    "❌ Cancel", callback_data=f"cancel_{task.file_id}")])
            elif task.status == "completed":
                keyboard.append([
                    InlineKeyboardButton(
                        "📋 View in Drive", url=f"https://drive.google.com/file/d/{task.drive_file_id}/view"),
                    InlineKeyboardButton(
                        "🗑️ Delete", callback_data=f"delete_{task.drive_file_id}")
                ])

            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

            # This would need the application context to update the message
            # For now, we'll just log the progress
            logger.info(
                f"Progress update for {task.file_name}: {task.progress}%")

        except Exception as e:
            logger.error(f"Failed to update progress message: {e}")

    async def process_upload_queue(self):
        """Process the upload queue"""
        while True:
            if self.upload_queue:
                task = self.upload_queue.pop(0)
                self.active_uploads[task.user_id] = task

                # Download file from Telegram
                file_path = await self.download_telegram_file(task)
                if not file_path:
                    task.status = "failed"
                    task.error = "Failed to download file from Telegram"
                    continue

                # Upload to Google Drive
                if task.file_size > 20 * 1024 * 1024:  # 20MB
                    success = await self.upload_file_chunked(task, file_path)
                else:
                    success = await self.upload_file_direct(task, file_path)

                # Clean up
                if os.path.exists(file_path):
                    os.remove(file_path)

                if task.user_id in self.active_uploads:
                    del self.active_uploads[task.user_id]

            await asyncio.sleep(1)

    async def download_telegram_file(self, task: UploadTask, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> Optional[str]:
        """Download file from Telegram to local storage."""
        try:
            file_path = os.path.join(UPLOAD_FOLDER, f"{task.file_id}_{task.file_name}")
            if context is None:
                raise RuntimeError("Telegram context is required to download real files")
            telegram_file = await context.bot.get_file(task.file_id)
            await telegram_file.download_to_drive(file_path)
            return file_path
        except Exception as e:
            logger.error(f"Failed to download file {task.file_name}: {e}")
            return None

    async def register_project_archive(self, user_id: int, file_path: str, file_name: str) -> ProjectRecord:
        """Upload a zip archive to Drive, inspect it, and record metadata."""
        if not zipfile.is_zipfile(file_path):
            raise ValueError("Project uploads must be ZIP archives")
        manager = self.get_drive_manager(user_id)
        if not manager.service:
            raise ValueError("Authentication required. Please use /login first.")

        extract_dir = os.path.join(tempfile.gettempdir(), f"project_scan_{uuid.uuid4().hex}")
        safe_extract_zip(file_path, extract_dir)
        detected = discover_project(extract_dir)
        shutil.rmtree(extract_dir, ignore_errors=True)

        base_name = Path(file_name).stem
        existing = self.registry.find_by_name(base_name)
        drive_file_id = manager.upload_file_chunked(file_path, file_name)
        if not drive_file_id:
            raise RuntimeError("Upload to Google Drive failed")

        if existing:
            version = len(existing.versions) + 1
            existing.versions.append(ProjectVersion(version, existing.drive_file_id, utc_now_iso(), file_name))
            existing.drive_file_id = drive_file_id
            existing.upload_date = utc_now_iso()
            existing.project_type = detected["project_type"] or "unknown"
            existing.main_entry_file = detected["main_entry_file"]
            existing.startup_command = detected["startup_command"]
            self.registry.save(existing)
            return existing

        project = ProjectRecord(
            project_id=uuid.uuid4().hex[:12],
            project_name=base_name,
            project_type=detected["project_type"] or "unknown",
            drive_file_id=drive_file_id,
            upload_date=utc_now_iso(),
            main_entry_file=detected["main_entry_file"],
            startup_command=detected["startup_command"],
        )
        self.registry.save(project)
        return project


# Initialize bot
bot = WOWDriveBot()

# Command handlers


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    welcome_text = """
🤖 **WOWDrive Bot** — Upload from Telegram to Google Drive

📋 **Commands**
• /start — Start the bot
• /help — Show this help message
• /login — Connect your Google Drive account
• /stat or /storage — Show your Drive storage usage
• /list — List your recent files
• /projects — List hosted projects
• /upload — Upload a ZIP project
• /start_project <id> — Start a project
• /stop_project <id> — Stop a project
• /restart_project <id> — Restart a project
• /project_info <id> — Show project details
• /logs <id> — Show recent logs
• /status — Show runtime status
• /recover — Restore running projects from Drive state
• /rename <fileId> <newName> — Rename a file
• /remove <fileId> — Delete a file
• /privacy — Privacy Policy & Terms

📤 **Upload Files**
• Send any document, photo, or video to upload to Drive
• Small files (≤20MB): Direct upload
• Large files (>20MB): Chunked upload with progress tracking
• Use buttons to cancel or view progress

⚡️ **Upload Process**
1️⃣ Request added to the queue!
2️⃣ Starting to upload...
3️⃣ Progress updates every 20 seconds
4️⃣ Upload completed!
"""
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    await start_command(update, context)


async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /login command"""
    user_id = update.effective_user.id

    auth_url = await bot.get_auth_url(user_id)
    if auth_url:
        message = f"""
🔐 **Google Drive Authentication**

Click the link below to authorize the bot:
{auth_url}

After authorization, you'll be redirected to complete the setup.
"""
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("❌ Failed to start authentication. Please try again later.")


async def stat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stat command"""
    user_id = update.effective_user.id

    storage_info = await bot.get_storage_info(user_id)
    if not storage_info:
        await update.message.reply_text("❌ Please authenticate first with /login")
        return

    total = int(storage_info.get('limit', 0))
    used = int(storage_info.get('usage', 0))
    free = total - used

    total_gb = total / (1024**3)
    used_gb = used / (1024**3)
    free_gb = free / (1024**3)

    usage_percent = (used / total * 100) if total > 0 else 0

    message = f"""
📊 **Drive Storage Usage**

💾 **Total Space:** {total_gb:.2f} GB
📈 **Used:** {used_gb:.2f} GB ({usage_percent:.1f}%)
🆓 **Free:** {free_gb:.2f} GB
"""
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command"""
    user_id = update.effective_user.id

    files = await bot.list_recent_files(user_id)
    if not files:
        await update.message.reply_text("❌ Please authenticate first with /login")
        return

    if not files:
        await update.message.reply_text("📁 No files found in your Drive")
        return

    message = "📁 **Recent Files:**\n\n"
    for i, file in enumerate(files[:10], 1):
        size = int(file.get('size', 0))
        size_mb = size / (1024**2) if size > 0 else 0
        created = file.get('createdTime', 'Unknown')

        message += f"{i}. **{file['name']}**\n"
        message += f"   📏 {size_mb:.1f} MB | 🆔 `{file['id']}`\n"
        message += f"   📅 {created[:10]}\n\n"

    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def rename_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /rename command"""
    user_id = update.effective_user.id
    args = context.args

    if len(args) < 2:
        await update.message.reply_text("Usage: /rename <fileId> <newName>")
        return

    file_id = args[0]
    new_name = ' '.join(args[1:])

    manager = bot.get_drive_manager(user_id)
    if not manager.service:
        await update.message.reply_text("❌ Please authenticate first with /login")
        return

    success = manager.rename_file(file_id, new_name)
    if success:
        await update.message.reply_text(f"✅ File renamed to '{new_name}'")
    else:
        await update.message.reply_text("❌ Failed to rename file")


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove command"""
    user_id = update.effective_user.id
    args = context.args

    if not args:
        await update.message.reply_text("Usage: /remove <fileId>")
        return

    file_id = args[0]

    manager = bot.get_drive_manager(user_id)
    if not manager.service:
        await update.message.reply_text("❌ Please authenticate first with /login")
        return

    success = manager.delete_file(file_id)
    if success:
        await update.message.reply_text("✅ File deleted successfully")
    else:
        await update.message.reply_text("❌ Failed to delete file")


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /privacy command"""
    privacy_text = """
🔒 **Privacy Policy & Terms**

**Data Collection:**
• We only store your Google Drive authentication tokens
• No file content is stored on our servers
• Files are uploaded directly to your Google Drive

**Data Usage:**
• Authentication tokens are used only for file operations
• No data is shared with third parties
• You can revoke access anytime from Google Account settings

**Terms of Service:**
• Use responsibly and in accordance with Google Drive ToS
• We're not responsible for your files or their content
• Service availability is not guaranteed

**Contact:**
For questions, contact the bot administrator.
"""
    await update.message.reply_text(privacy_text, parse_mode=ParseMode.MARKDOWN)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document uploads for both normal Drive files and project ZIPs."""
    user_id = update.effective_user.id
    document = update.message.document
    if not document:
        return
    if document.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"❌ File too large! Maximum size is {MAX_FILE_SIZE // (1024**3)}GB")
        return

    task = UploadTask(
        user_id=user_id,
        file_id=document.file_id,
        file_name=document.file_name or f"document_{document.file_id}",
        file_size=document.file_size,
        message_id=update.message.message_id
    )
    await update.message.reply_text(f"📥 Downloading **{task.file_name}**...", parse_mode=ParseMode.MARKDOWN)
    file_path = await bot.download_telegram_file(task, context)
    if not file_path:
        await update.message.reply_text("❌ Failed to download file from Telegram")
        return
    try:
        if task.file_name.lower().endswith(".zip") and is_owner(update):
            project = await bot.register_project_archive(user_id, file_path, task.file_name)
            await update.message.reply_text(
                f"✅ Project registered: **{project.project_name}**\nID: `{project.project_id}`\nType: {project.project_type}\nEntry: `{project.main_entry_file}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            success = await (bot.upload_file_chunked(task, file_path) if task.file_size > 20 * 1024 * 1024 else bot.upload_file_direct(task, file_path))
            await update.message.reply_text("✅ Upload completed" if success else f"❌ Upload failed: {task.error}")
    except Exception as exc:
        logger.exception("Document handling failed")
        await update.message.reply_text(f"❌ Upload failed: {exc}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo uploads"""
    user_id = update.effective_user.id
    photo = update.message.photo[-1]  # Get highest resolution

    if not photo:
        return

    # Check file size
    if photo.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"❌ File too large! Maximum size is {MAX_FILE_SIZE // (1024**3)}GB")
        return

    # Create upload task
    task = UploadTask(
        user_id=user_id,
        file_id=photo.file_id,
        file_name=f"photo_{photo.file_id}.jpg",
        file_size=photo.file_size,
        message_id=update.message.message_id
    )

    bot.upload_queue.append(task)

    # Send initial message
    message = f"📤 **{task.file_name}**\n\n⏳ Request added to the queue!"
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video uploads"""
    user_id = update.effective_user.id
    video = update.message.video

    if not video:
        return

    # Check file size
    if video.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"❌ File too large! Maximum size is {MAX_FILE_SIZE // (1024**3)}GB")
        return

    # Create upload task
    task = UploadTask(
        user_id=user_id,
        file_id=video.file_id,
        file_name=video.file_name or f"video_{video.file_id}.mp4",
        file_size=video.file_size,
        message_id=update.message.message_id
    )

    bot.upload_queue.append(task)

    # Send initial message
    message = f"📤 **{task.file_name}**\n\n⏳ Request added to the queue!"
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


def is_owner(update: Update) -> bool:
    """Limit hosting controls to one trusted owner when OWNER_USER_ID is configured."""
    return not OWNER_USER_ID or str(update.effective_user.id) == str(OWNER_USER_ID)


async def require_owner(update: Update) -> bool:
    if is_owner(update):
        return True
    await update.message.reply_text("❌ This personal hosting bot is restricted to the owner.")
    return False


async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Send a .zip file as a Telegram document to upload and register a project.")


async def projects_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    projects = bot.registry.list_projects()
    if not projects:
        await update.message.reply_text("📁 No projects registered yet. Use /upload and send a ZIP archive.")
        return
    lines = ["📁 **Projects**"]
    for project in projects:
        lines.append(f"• `{project.project_id}` — **{project.project_name}** ({project.project_type}) — {project.status}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


def _project_id_from_args(context):
    return context.args[0] if context.args else None


async def start_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    project_id = _project_id_from_args(context)
    if not project_id:
        await update.message.reply_text("Usage: /start_project <project_id>")
        return
    try:
        result = await bot.project_manager.start_project(project_id, bot.get_drive_manager(update.effective_user.id), auto_restart=True)
        await update.message.reply_text(f"✅ Project {result}")
    except Exception as exc:
        logger.exception("Failed to start project")
        await update.message.reply_text(f"❌ Failed to start project: {exc}")


async def stop_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    project_id = _project_id_from_args(context)
    if not project_id:
        await update.message.reply_text("Usage: /stop_project <project_id>")
        return
    result = await bot.project_manager.stop_project(project_id, bot.get_drive_manager(update.effective_user.id))
    await update.message.reply_text(f"✅ Project {result}")


async def restart_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    project_id = _project_id_from_args(context)
    if not project_id:
        await update.message.reply_text("Usage: /restart_project <project_id>")
        return
    result = await bot.project_manager.restart_project(project_id, bot.get_drive_manager(update.effective_user.id))
    await update.message.reply_text(f"✅ Project {result}")


async def delete_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    project_id = _project_id_from_args(context)
    delete_drive = len(context.args) > 1 and context.args[1] == "--drive"
    if not project_id:
        await update.message.reply_text("Usage: /delete_project <project_id> [--drive]")
        return
    await bot.project_manager.stop_project(project_id, bot.get_drive_manager(update.effective_user.id))
    project = bot.registry.delete(project_id)
    if project and delete_drive:
        bot.get_drive_manager(update.effective_user.id).delete_file(project.drive_file_id)
    await update.message.reply_text("✅ Project deleted" if project else "❌ Project not found")


async def project_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    project_id = _project_id_from_args(context)
    project = bot.registry.get(project_id) if project_id else None
    if not project:
        await update.message.reply_text("Usage: /project_info <project_id>")
        return
    await update.message.reply_text(
        f"ℹ️ **{project.project_name}**\nID: `{project.project_id}`\nType: {project.project_type}\nEntry: `{project.main_entry_file}`\nStatus: {project.status}\nAuto restart: {project.auto_restart}\nDrive file: `{project.drive_file_id}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    project_id = _project_id_from_args(context)
    if not project_id:
        await update.message.reply_text("Usage: /logs <project_id>")
        return
    text = bot.log_store.tail(project_id, 80)
    await update.message.reply_text(f"```\n{text[-3500:]}\n```", parse_mode=ParseMode.MARKDOWN)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    lines = bot.project_manager.status_lines()
    await update.message.reply_text("🟢 **Status**\n" + ("\n".join(lines) if lines else "No projects."), parse_mode=ParseMode.MARKDOWN)


async def storage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stat_command(update, context)


async def recover_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    count = await bot.project_manager.recover(bot.get_drive_manager(update.effective_user.id))
    await update.message.reply_text(f"✅ Recovery complete. Restored {count} project(s).")


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries from inline keyboards"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("cancel_"):
        file_id = data.replace("cancel_", "")
        # Handle cancel logic
        await query.edit_message_text("❌ Upload cancelled")

    elif data.startswith("delete_"):
        drive_file_id = data.replace("delete_", "")
        # Handle delete logic
        await query.edit_message_text("🗑️ File deleted from Drive")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Update {update} caused error {context.error}")


async def post_init_recovery(application: Application):
    """Automatically restore desired running projects after Railway restarts."""
    if not OWNER_USER_ID:
        logger.info("OWNER_USER_ID not configured; skipping automatic project recovery")
        return
    manager = bot.get_drive_manager(int(OWNER_USER_ID))
    if not manager.service:
        logger.warning("Owner Google Drive credentials unavailable; automatic recovery skipped")
        return
    recovered = await bot.project_manager.recover(manager)
    logger.info("Automatic recovery restored %s project(s)", recovered)


def start_bot():
    """Start the Telegram bot"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not found in environment variables")
        return

    # Create application
    application = Application.builder().token(BOT_TOKEN).post_init(post_init_recovery).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("login", login_command))
    application.add_handler(CommandHandler("stat", stat_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("rename", rename_command))
    application.add_handler(CommandHandler("remove", remove_command))
    application.add_handler(CommandHandler("privacy", privacy_command))
    application.add_handler(CommandHandler("projects", projects_command))
    application.add_handler(CommandHandler("upload", upload_command))
    application.add_handler(CommandHandler("start_project", start_project_command))
    application.add_handler(CommandHandler("stop_project", stop_project_command))
    application.add_handler(CommandHandler("restart_project", restart_project_command))
    application.add_handler(CommandHandler("delete_project", delete_project_command))
    application.add_handler(CommandHandler("project_info", project_info_command))
    application.add_handler(CommandHandler("logs", logs_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("storage", storage_command))
    application.add_handler(CommandHandler("recover", recover_command))

    # Message handlers
    application.add_handler(MessageHandler(
        filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))

    # Callback query handler
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    # Error handler
    application.add_error_handler(error_handler)

    # Start the bot
    logger.info("Starting WOWDrive Bot...")
    application.run_polling()
