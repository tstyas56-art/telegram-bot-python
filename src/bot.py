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
from translations.ar import t

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
        self.registry = ProjectRegistry(PROJECT_REGISTRY_FILE, PROJECT_REGISTRY_DRIVE_NAME)
        self.runtime_store = RuntimeStateStore(RUNTIME_STATE_FILE, RUNTIME_STATE_DRIVE_NAME)
        self.log_store = ProjectLogStore(PROJECT_LOG_DIR)
        self.application = None
        self.project_manager = ProjectManager(
            self.registry, self.runtime_store, PROJECT_WORKSPACE, self.log_store, self.notify_owner
        )


    async def notify_owner(self, message: str) -> None:
        """Send automatic operational alerts to the owner chat when configured."""
        if not OWNER_CHAT_ID or not self.application:
            return
        try:
            await self.application.bot.send_message(chat_id=OWNER_CHAT_ID, text=message)
        except Exception as exc:
            logger.error("فشل إرسال إشعار للمالك: %s", exc)
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
            task.error = t("auth_required")
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
                task.error = t("upload_failed", error="Google Drive")
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
            task.error = t("auth_required")
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
                task.error = t("upload_failed", error="Google Drive")
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
                text = t("queued", file_name=task.file_name)
            elif task.status == "uploading":
                text = t("uploading", file_name=task.file_name, progress=task.progress)
            elif task.status == "completed":
                text = t("completed", file_name=task.file_name, drive_file_id=task.drive_file_id)
            elif task.status == "failed":
                text = t("failed", file_name=task.file_name, error=task.error)
            else:
                text = t("status_generic", file_name=task.file_name, status=task.status)

            keyboard = []
            if task.status == "uploading":
                keyboard.append([InlineKeyboardButton(
                    t("cancel_button"), callback_data=f"cancel_{task.file_id}")])
            elif task.status == "completed":
                keyboard.append([
                    InlineKeyboardButton(
                        t("view_drive_button"), url=f"https://drive.google.com/file/d/{task.drive_file_id}/view"),
                    InlineKeyboardButton(
                        t("delete_button"), callback_data=f"delete_{task.drive_file_id}")
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
                raise RuntimeError("سياق تيليجرام مطلوب لتنزيل الملفات الحقيقية")
            telegram_file = await context.bot.get_file(task.file_id)
            await telegram_file.download_to_drive(file_path)
            return file_path
        except Exception as e:
            logger.error(f"Failed to download file {task.file_name}: {e}")
            return None

    async def register_project_archive(self, user_id: int, file_path: str, file_name: str) -> ProjectRecord:
        """Upload a zip archive to Drive, inspect it, and record metadata."""
        if not zipfile.is_zipfile(file_path):
            raise ValueError("يجب أن يكون المشروع ملف ZIP")
        manager = self.get_drive_manager(user_id)
        if not manager.service:
            raise ValueError(t("auth_required"))

        extract_dir = os.path.join(tempfile.gettempdir(), f"project_scan_{uuid.uuid4().hex}")
        safe_extract_zip(file_path, extract_dir)
        detected = discover_project(extract_dir)
        shutil.rmtree(extract_dir, ignore_errors=True)

        base_name = Path(file_name).stem
        existing = self.registry.find_by_name(base_name)
        drive_file_id = manager.upload_file_chunked(file_path, file_name)
        if not drive_file_id:
            raise RuntimeError("فشل الرفع إلى Google Drive")

        if existing:
            version = len(existing.versions) + 1
            existing.versions.append(ProjectVersion(version, existing.drive_file_id, utc_now_iso(), file_name))
            while len(existing.versions) > MAX_PROJECT_VERSIONS:
                old_version = existing.versions.pop(0)
                manager.delete_file(old_version.drive_file_id)
                logger.info("تم حذف إصدار قديم من المشروع %s من Google Drive: %s", existing.project_name, old_version.drive_file_id)
            existing.drive_file_id = drive_file_id
            existing.upload_date = utc_now_iso()
            existing.project_type = detected["project_type"] or "unknown"
            existing.main_entry_file = detected["main_entry_file"]
            existing.startup_command = detected["startup_command"]
            self.registry.save(existing, manager)
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
        self.registry.save(project, manager)
        return project


# Initialize bot
bot = WOWDriveBot()

# Command handlers


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(t("start_help"))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    await start_command(update, context)


async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /login command."""
    user_id = update.effective_user.id
    auth_url = await bot.get_auth_url(user_id)
    if auth_url:
        await update.message.reply_text(t("login_message", auth_url=auth_url))
    else:
        await update.message.reply_text(t("login_failed"))


async def stat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stat command."""
    user_id = update.effective_user.id
    storage_info = await bot.get_storage_info(user_id)
    if not storage_info:
        await update.message.reply_text(t("auth_required"))
        return
    total = int(storage_info.get('limit', 0))
    used = int(storage_info.get('usage', 0))
    free = total - used
    usage_percent = (used / total * 100) if total > 0 else 0
    await update.message.reply_text(t("storage_info", total=total/(1024**3), used=used/(1024**3), free=free/(1024**3), percent=usage_percent))


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command."""
    user_id = update.effective_user.id
    files = await bot.list_recent_files(user_id)
    if not files:
        await update.message.reply_text(t("auth_required"))
        return
    message = t("recent_files_header")
    for i, file in enumerate(files[:10], 1):
        size = int(file.get('size', 0))
        size_mb = size / (1024**2) if size > 0 else 0
        created = file.get('createdTime', 'غير معروف')
        message += f"{i}. {file['name']}\n"
        message += f"   📏 {size_mb:.1f} MB | 🆔 {file['id']}\n"
        message += f"   📅 {created[:10]}\n\n"
    await update.message.reply_text(message)


async def rename_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /rename command."""
    user_id = update.effective_user.id
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(t("rename_usage"))
        return
    file_id = args[0]
    new_name = ' '.join(args[1:])
    manager = bot.get_drive_manager(user_id)
    if not manager.service:
        await update.message.reply_text(t("auth_required"))
        return
    success = manager.rename_file(file_id, new_name)
    await update.message.reply_text(t("renamed", name=new_name) if success else t("rename_failed"))


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove command."""
    user_id = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text(t("remove_usage"))
        return
    manager = bot.get_drive_manager(user_id)
    if not manager.service:
        await update.message.reply_text(t("auth_required"))
        return
    success = manager.delete_file(args[0])
    await update.message.reply_text(t("removed") if success else t("remove_failed"))


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /privacy command."""
    await update.message.reply_text(t("privacy"))


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document uploads for both normal Drive files and project ZIPs."""
    user_id = update.effective_user.id
    document = update.message.document
    if not document:
        return
    if document.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(t("file_too_large", max_gb=MAX_FILE_SIZE // (1024**3)))
        return

    task = UploadTask(
        user_id=user_id,
        file_id=document.file_id,
        file_name=document.file_name or f"document_{document.file_id}",
        file_size=document.file_size,
        message_id=update.message.message_id
    )
    await update.message.reply_text(t("download_started", file_name=task.file_name))
    file_path = await bot.download_telegram_file(task, context)
    if not file_path:
        await update.message.reply_text(t("download_failed"))
        return
    try:
        if task.file_name.lower().endswith(".zip") and is_owner(update):
            project = await bot.register_project_archive(user_id, file_path, task.file_name)
            await update.message.reply_text(
                t("project_registered", name=project.project_name, id=project.project_id, type=project.project_type, entry=project.main_entry_file)
            )
        else:
            success = await (bot.upload_file_chunked(task, file_path) if task.file_size > 20 * 1024 * 1024 else bot.upload_file_direct(task, file_path))
            await update.message.reply_text(t("upload_completed") if success else t("upload_failed", error=task.error))
    except Exception as exc:
        logger.exception("Document handling failed")
        await update.message.reply_text(t("upload_failed", error=exc))
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
        await update.message.reply_text(t("file_too_large", max_gb=MAX_FILE_SIZE // (1024**3)))
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
    message = t("queued", file_name=task.file_name)
    await update.message.reply_text(message)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video uploads"""
    user_id = update.effective_user.id
    video = update.message.video

    if not video:
        return

    # Check file size
    if video.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(t("file_too_large", max_gb=MAX_FILE_SIZE // (1024**3)))
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
    message = t("queued", file_name=task.file_name)
    await update.message.reply_text(message)


def is_owner(update: Update) -> bool:
    """Limit hosting controls to one trusted owner when OWNER_USER_ID is configured."""
    return not OWNER_USER_ID or str(update.effective_user.id) == str(OWNER_USER_ID)


async def require_owner(update: Update) -> bool:
    if is_owner(update):
        return True
    await update.message.reply_text(t("owner_only"))
    return False


async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(t("upload_instruction"))


async def projects_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    projects = bot.registry.list_projects()
    if not projects:
        await update.message.reply_text(t("no_projects"))
        return
    lines = [t("projects_header")]
    for project in projects:
        lines.append(f"• {project.project_id} — {project.project_name} ({project.project_type}) — {project.status}")
    await update.message.reply_text("\n".join(lines))


def _project_id_from_args(context):
    return context.args[0] if context.args else None


async def start_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    project_id = _project_id_from_args(context)
    if not project_id:
        await update.message.reply_text(t("start_usage"))
        return
    try:
        result = await bot.project_manager.start_project(project_id, bot.get_drive_manager(update.effective_user.id), auto_restart=True, entry_file=(context.args[1] if len(context.args) > 1 else None))
        await update.message.reply_text(t("project_started", result=result))
    except Exception as exc:
        logger.exception("Failed to start project")
        await update.message.reply_text(t("operation_failed", error=exc))


async def stop_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    project_id = _project_id_from_args(context)
    if not project_id:
        await update.message.reply_text(t("stop_usage"))
        return
    result = await bot.project_manager.stop_project(project_id, bot.get_drive_manager(update.effective_user.id))
    await update.message.reply_text(t("project_started", result=result))


async def restart_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    project_id = _project_id_from_args(context)
    if not project_id:
        await update.message.reply_text(t("restart_usage"))
        return
    result = await bot.project_manager.restart_project(project_id, bot.get_drive_manager(update.effective_user.id), entry_file=(context.args[1] if len(context.args) > 1 else None))
    await update.message.reply_text(t("project_started", result=result))


async def delete_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    project_id = _project_id_from_args(context)
    delete_drive = len(context.args) > 1 and context.args[1] == "--drive"
    if not project_id:
        await update.message.reply_text(t("delete_usage"))
        return
    await bot.project_manager.stop_project(project_id, bot.get_drive_manager(update.effective_user.id))
    project = bot.registry.delete(project_id, bot.get_drive_manager(update.effective_user.id))
    if project and delete_drive:
        bot.get_drive_manager(update.effective_user.id).delete_file(project.drive_file_id)
    await update.message.reply_text(t("project_deleted") if project else t("project_not_found"))


async def project_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    project_id = _project_id_from_args(context)
    project = bot.registry.get(project_id) if project_id else None
    if not project:
        await update.message.reply_text(t("info_usage"))
        return
    await update.message.reply_text(
        t("project_info", name=project.project_name, id=project.project_id, type=project.project_type, entry=project.main_entry_file, status=project.status, auto_restart=project.auto_restart, drive_file_id=project.drive_file_id)
    )


async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    project_id = _project_id_from_args(context)
    if not project_id:
        await update.message.reply_text(t("logs_usage"))
        return
    lines = int(context.args[1]) if len(context.args) > 1 and context.args[1] in {"50", "100", "500"} else 100
    text = bot.log_store.tail(project_id, lines)
    await update.message.reply_text(f"```\n{text[-3500:]}\n```")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    lines = bot.project_manager.status_lines()
    await update.message.reply_text(t("status_header", lines=("\n".join(lines) if lines else t("no_running"))))


async def storage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stat_command(update, context)


async def recover_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    count = await bot.project_manager.recover(bot.get_drive_manager(update.effective_user.id))
    await update.message.reply_text(t("recover_done", count=count))


async def running_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    lines = bot.project_manager.running_lines()
    await update.message.reply_text(t("status_header", lines=("\n".join(lines) if lines else t("no_running"))))


async def resources_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    lines = bot.project_manager.resource_lines()
    await update.message.reply_text(t("resources_header", lines=("\n".join(lines) if lines else t("no_running"))))


async def uptime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    lines = bot.project_manager.uptime_lines()
    await update.message.reply_text(t("uptime_header", lines=("\n".join(lines) if lines else t("no_running"))))


async def stop_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    count = await bot.project_manager.stop_all(bot.get_drive_manager(update.effective_user.id))
    await update.message.reply_text(t("all_stopped", count=count))


async def restart_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    try:
        count = await bot.project_manager.restart_all(bot.get_drive_manager(update.effective_user.id))
        await update.message.reply_text(t("all_restarted", count=count))
    except Exception as exc:
        logger.exception("فشل إعادة تشغيل كل المشاريع")
        await update.message.reply_text(t("operation_failed", error=exc))


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    manager = bot.get_drive_manager(update.effective_user.id)
    if not manager.service:
        await update.message.reply_text(t("auth_required"))
        return
    bot.registry.backup_to_drive(manager)
    bot.runtime_store.save(bot.project_manager.running_state, manager)
    bot.log_store.archive_all([project.project_id for project in bot.registry.list_projects()], manager)
    await update.message.reply_text(t("backup_done"))


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    lines = bot.project_manager.health_lines(bot.get_drive_manager(update.effective_user.id))
    await update.message.reply_text(t("health_header", lines="\n".join(lines)))


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries from inline keyboards"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("cancel_"):
        file_id = data.replace("cancel_", "")
        # Handle cancel logic
        await query.edit_message_text(t("cancelled"))

    elif data.startswith("delete_"):
        drive_file_id = data.replace("delete_", "")
        # Handle delete logic
        await query.edit_message_text(t("drive_deleted"))


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Update {update} caused error {context.error}")


async def post_init_recovery(application: Application):
    """Automatically restore desired running projects after Railway restarts."""
    bot.application = application
    if not OWNER_USER_ID:
        logger.info("لم يتم ضبط OWNER_USER_ID؛ سيتم تخطي الاستعادة التلقائية")
        return
    manager = bot.get_drive_manager(int(OWNER_USER_ID))
    if not manager.service:
        logger.warning("بيانات اعتماد Google Drive للمالك غير متاحة؛ تم تخطي الاستعادة التلقائية")
        await bot.notify_owner("❌ Google Drive غير متصل؛ تم تخطي الاستعادة التلقائية.")
        return
    recovered = await bot.project_manager.recover(manager)
    logger.info("الاستعادة التلقائية شغّلت %s مشروع/مشاريع", recovered)


def start_bot():
    """Start the Telegram bot"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN غير موجود في متغيرات البيئة")
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
    application.add_handler(CommandHandler("running", running_command))
    application.add_handler(CommandHandler("resources", resources_command))
    application.add_handler(CommandHandler("uptime", uptime_command))
    application.add_handler(CommandHandler("stop_all", stop_all_command))
    application.add_handler(CommandHandler("restart_all", restart_all_command))
    application.add_handler(CommandHandler("backup", backup_command))
    application.add_handler(CommandHandler("health", health_command))

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
    logger.info("بدء تشغيل WOWDrive Bot...")
    application.run_polling()
