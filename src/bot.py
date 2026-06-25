#!/usr/bin/env python3
"""
ZEUS Uptime Bot - Upload files from Telegram to Google Drive
هيكلة جديدة: واجهة قائمة بالأزرار (Inline Keyboards) بدل الأوامر النصية اليدوية،
مع الحفاظ الكامل على منطق العمل (Drive / ProjectManager / Registry) كما هو.
"""

import asyncio
import logging
import os
import tempfile
import shutil
import uuid
import zipfile
import aiohttp
from typing import Dict, Optional, List
from dataclasses import dataclass
from pathlib import Path

from asyncio_throttle import Throttler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

from config import *
from drive import GoogleDriveManager
from logs.project_logs import ProjectLogStore
from models import ProjectRecord, ProjectVersion, utc_now_iso
from project_manager.discovery import discover_project, safe_extract_zip
from project_manager.manager import ProjectManager
from runtime.state import RuntimeStateStore
from storage.project_registry import ProjectRegistry
from translations.ar import t

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────
# Callback-data namespace (keep every value short: Telegram limit = 64 bytes)
#   menu:<name>                  -> open a menu
#   proj:<project_id>            -> open the action-menu for one project
#   act:<action>:<project_id>    -> run an action on a project
#   conf:<action>:<project_id>   -> ask for confirmation before destructive act
#   page:<menu>:<index>          -> pagination
#   noop                         -> button that does nothing (page indicator)
# ──────────────────────────────────────────────────────────────────────────

PROJECTS_PER_PAGE = 6
DESTRUCTIVE_ACTIONS = {"stop_all", "restart_all", "delete", "delete_drive"}
ENTRY_CANDIDATES: Dict[str, List[str]] = {}
DEPENDENCY_CANDIDATES: Dict[str, List[str]] = {}


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


# ──────────────────────────────────────────────────────────────────────────
# Core bot object — unchanged business logic, only the surface area differs
# ──────────────────────────────────────────────────────────────────────────

class ZEUSUptimeBot:
    def __init__(self):
        self.upload_queue: List[UploadTask] = []
        self.active_uploads: Dict[int, UploadTask] = {}
        self.user_drive_managers: Dict[int, GoogleDriveManager] = {}
        self.throttler = Throttler(rate_limit=RATE_LIMIT_REQUESTS, period=RATE_LIMIT_PERIOD)

        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        self.registry = ProjectRegistry(PROJECT_REGISTRY_FILE, PROJECT_REGISTRY_DRIVE_NAME)
        self.runtime_store = RuntimeStateStore(RUNTIME_STATE_FILE, RUNTIME_STATE_DRIVE_NAME)
        self.log_store = ProjectLogStore(PROJECT_LOG_DIR)
        self.application: Optional[Application] = None
        self.project_manager = ProjectManager(
            self.registry, self.runtime_store, PROJECT_WORKSPACE, self.log_store, self.notify_owner
        )

    # ---- infra -----------------------------------------------------------

    async def notify_owner(self, message: str) -> None:
        if not OWNER_CHAT_ID or not self.application:
            return
        try:
            await self.application.bot.send_message(chat_id=OWNER_CHAT_ID, text=message)
        except Exception as exc:
            logger.error("فشل إرسال إشعار للمالك: %s", exc)

    def get_drive_manager(self, user_id: int) -> GoogleDriveManager:
        user_key = str(user_id)
        manager = self.user_drive_managers.get(user_id)
        if manager is None:
            manager = GoogleDriveManager()
            self.user_drive_managers[user_id] = manager
        if manager.service is None:
            manager.load_credentials_from_file(user_key)
        return manager

    async def get_auth_url(self, user_id: int) -> Optional[str]:
        try:
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
        return self.get_drive_manager(user_id).service is not None

    async def get_storage_info(self, user_id: int) -> Optional[Dict]:
        return self.get_drive_manager(user_id).get_storage_info()

    async def list_recent_files(self, user_id: int, limit: int = 10) -> List[Dict]:
        return self.get_drive_manager(user_id).list_files(limit)

    # ---- uploads -----------------------------------------------------------

    async def upload_file_chunked(self, task: UploadTask, file_path: str) -> bool:
        manager = self.get_drive_manager(task.user_id)
        if not manager.service:
            task.error = t("auth_required")
            return False
        try:
            task.status = "uploading"
            drive_file_id = manager.upload_file_chunked(
                file_path, task.file_name, lambda p: setattr(task, "progress", p)
            )
            if drive_file_id:
                task.drive_file_id = drive_file_id
                task.status = "completed"
                task.progress = 100
                return True
            task.error = t("upload_failed", error="Google Drive")
            task.status = "failed"
            return False
        except Exception as e:
            logger.error(f"Upload failed for task {task.file_id}: {e}")
            task.error = str(e)
            task.status = "failed"
            return False

    async def upload_file_direct(self, task: UploadTask, file_path: str) -> bool:
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
                return True
            task.error = t("upload_failed", error="Google Drive")
            task.status = "failed"
            return False
        except Exception as e:
            logger.error(f"Direct upload failed for task {task.file_id}: {e}")
            task.error = str(e)
            task.status = "failed"
            return False

    async def download_telegram_file(self, task: UploadTask, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> Optional[str]:
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
        if not zipfile.is_zipfile(file_path):
            raise ValueError("يجب أن يكون المشروع ملف ZIP")
        manager = self.get_drive_manager(user_id)
        if not manager.service:
            raise ValueError(t("auth_required"))

        extract_dir = os.path.join(tempfile.gettempdir(), f"project_scan_{uuid.uuid4().hex}")
        safe_extract_zip(file_path, extract_dir)
        detected = discover_project(extract_dir)
        shutil.rmtree(extract_dir, ignore_errors=True)

        return self._save_project_record(user_id, file_path, file_name, detected)

    async def register_python_file_project(self, user_id: int, file_path: str, file_name: str) -> ProjectRecord:
        """Package one .py file as a runnable project and register it."""
        manager = self.get_drive_manager(user_id)
        if not manager.service:
            raise ValueError(t("auth_required"))
        safe_name = Path(file_name).name
        if not safe_name.lower().endswith(".py"):
            raise ValueError("يجب أن يكون الملف بصيغة .py")
        archive_path = os.path.join(tempfile.gettempdir(), f"single_py_{uuid.uuid4().hex}.zip")
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(file_path, safe_name)
        try:
            detected = {
                "project_type": "single_python_file",
                "main_entry_file": safe_name,
                "startup_command": ["python", safe_name],
                "dependency_file": None,
            }
            return self._save_project_record(user_id, archive_path, f"{Path(safe_name).stem}.zip", detected)
        finally:
            if os.path.exists(archive_path):
                os.remove(archive_path)

    def _save_project_record(self, user_id: int, archive_path: str, archive_name: str, detected: Dict) -> ProjectRecord:
        manager = self.get_drive_manager(user_id)
        base_name = Path(archive_name).stem
        existing = self.registry.find_by_name(base_name)
        drive_file_id = manager.upload_file_chunked(archive_path, archive_name)
        if not drive_file_id:
            raise RuntimeError("فشل الرفع إلى Google Drive")

        if existing:
            version = len(existing.versions) + 1
            existing.versions.append(ProjectVersion(version, existing.drive_file_id, utc_now_iso(), archive_name))
            while len(existing.versions) > MAX_PROJECT_VERSIONS:
                old_version = existing.versions.pop(0)
                manager.delete_file(old_version.drive_file_id)
                logger.info("تم حذف إصدار قديم من المشروع %s من Google Drive: %s", existing.project_name, old_version.drive_file_id)
            existing.drive_file_id = drive_file_id
            existing.upload_date = utc_now_iso()
            existing.project_type = detected["project_type"] or "unknown"
            existing.main_entry_file = detected["main_entry_file"]
            existing.startup_command = detected["startup_command"]
            existing.dependency_file = detected.get("dependency_file")
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
            dependency_file=detected.get("dependency_file"),
        )
        self.registry.save(project, manager)
        return project


bot = ZEUSUptimeBot()


# ──────────────────────────────────────────────────────────────────────────
# Permission helpers
# ──────────────────────────────────────────────────────────────────────────

def is_owner(update: Update) -> bool:
    return not OWNER_USER_ID or str(update.effective_user.id) == str(OWNER_USER_ID)


async def require_owner_cb(query) -> bool:
    if not OWNER_USER_ID or str(query.from_user.id) == str(OWNER_USER_ID):
        return True
    await query.answer(t("owner_only"), show_alert=True)
    return False


async def require_owner(update: Update) -> bool:
    if is_owner(update):
        return True
    await update.message.reply_text(t("owner_only"))
    return False


# ──────────────────────────────────────────────────────────────────────────
# Menu builders (with enhanced colors & aesthetics)
# ──────────────────────────────────────────────────────────────────────────

def main_menu_keyboard(owner: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔵 ☁️ Google Drive", callback_data="menu:drive")],
    ]
    if owner:
        rows.append([InlineKeyboardButton("🟢 📦 المشاريع المستضافة", callback_data="menu:projects:0")])
        rows.append([InlineKeyboardButton("🟠 🛰️ لوحة التحكم بالنظام", callback_data="menu:system")])
    rows.append([InlineKeyboardButton("⚪ 🔒 الخصوصية", callback_data="menu:privacy")])
    return InlineKeyboardMarkup(rows)


def drive_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 تسجيل الدخول", callback_data="act:login:_")],
        [InlineKeyboardButton("📊 المساحة المتاحة", callback_data="act:stat:_")],
        [InlineKeyboardButton("📁 آخر الملفات", callback_data="act:list:_")],
        [InlineKeyboardButton("⬆️ كيف أرفع ملفًا؟", callback_data="act:upload_help:_")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="menu:main")],
    ])


def system_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 الحالة العامة", callback_data="act:status:_"),
         InlineKeyboardButton("🩺 الصحة", callback_data="act:health:_")],
        [InlineKeyboardButton("📈 الموارد", callback_data="act:resources:_"),
         InlineKeyboardButton("⏱️ مدة التشغيل", callback_data="act:uptime:_")],
        [InlineKeyboardButton("♻️ استرجاع المشاريع", callback_data="act:recover:_")],
        [InlineKeyboardButton("⏹️ إيقاف الكل", callback_data="conf:stop_all:_"),
         InlineKeyboardButton("🔁 إعادة تشغيل الكل", callback_data="conf:restart_all:_")],
        [InlineKeyboardButton("💾 نسخة احتياطية", callback_data="act:backup:_")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="menu:main")],
    ])


def projects_list_keyboard(page: int) -> InlineKeyboardMarkup:
    projects = bot.registry.list_projects()
    start = page * PROJECTS_PER_PAGE
    chunk = projects[start:start + PROJECTS_PER_PAGE]

    rows = []
    for p in chunk:
        icon = "🟢" if getattr(p, "status", "") == "running" else ("🔴" if getattr(p, "status", "") == "error" else "⚪")
        rows.append([InlineKeyboardButton(
            f"{icon} {p.project_name} ({p.project_type})",
            callback_data=f"proj:{p.project_id}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"menu:projects:{page-1}"))
    if start + PROJECTS_PER_PAGE < len(projects):
        nav.append(InlineKeyboardButton("▶️", callback_data=f"menu:projects:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("⬆️ رفع مشروع جديد (ZIP أو PY)", callback_data="act:upload_help:_")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def project_action_keyboard(project_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ تشغيل", callback_data=f"act:start:{project_id}"),
         InlineKeyboardButton("⏹️ إيقاف", callback_data=f"act:stop:{project_id}")],
        [InlineKeyboardButton("🎯 اختيار ملف التشغيل", callback_data=f"act:choose_entry:{project_id}"),
         InlineKeyboardButton("📦 ملف المكتبات", callback_data=f"act:choose_deps:{project_id}")],
        [InlineKeyboardButton("⚙️ متغيرات البيئة", callback_data=f"act:env:{project_id}")],
        [InlineKeyboardButton("🔁 إعادة تشغيل", callback_data=f"act:restart:{project_id}")],
        [InlineKeyboardButton("ℹ️ معلومات", callback_data=f"act:info:{project_id}"),
         InlineKeyboardButton("📜 السجلات", callback_data=f"act:logs:{project_id}")],
        [InlineKeyboardButton("🗑️ حذف", callback_data=f"conf:delete:{project_id}"),
         InlineKeyboardButton("🗑️☁️ حذف + من Drive", callback_data=f"conf:delete_drive:{project_id}")],
        [InlineKeyboardButton("⬅️ كل المشاريع", callback_data="menu:projects:0")],
    ])


def python_entry_candidates(project_dir: Path) -> List[str]:
    """Return likely Python entry files inside an extracted project."""
    ignored_dirs = {".git", "__pycache__", "venv", ".venv", "env", "node_modules", "site-packages"}
    candidates = []
    preferred = {"main.py": 0, "bot.py": 1, "app.py": 2, "run.py": 3, "server.py": 4, "index.py": 5}
    for path in project_dir.rglob("*.py"):
        rel = path.relative_to(project_dir)
        if any(part in ignored_dirs for part in rel.parts):
            continue
        candidates.append(str(rel).replace(os.sep, "/"))
    candidates.sort(key=lambda x: (preferred.get(Path(x).name, 50), len(Path(x).parts), x.lower()))
    return candidates[:40]


def entry_selection_keyboard(project_id: str, entries: List[str]) -> InlineKeyboardMarkup:
    rows = []
    for idx, entry in enumerate(entries[:30]):
        label = entry if len(entry) <= 45 else "…" + entry[-44:]
        rows.append([InlineKeyboardButton(f"🐍 {label}", callback_data=f"entry:{project_id}:{idx}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع للمشروع", callback_data=f"proj:{project_id}")])
    return InlineKeyboardMarkup(rows)


async def show_entry_selector(query, project_id: str, manager, message_prefix: str = "") -> None:
    project = bot.registry.get(project_id)
    if not project:
        await query.edit_message_text(t("project_not_found"))
        return
    try:
        project_dir = await bot.project_manager.ensure_project_files(project, manager)
        entries = python_entry_candidates(project_dir)
    except Exception as exc:
        logger.exception("Failed to prepare entry selector")
        await query.edit_message_text(t("operation_failed", error=exc), reply_markup=project_action_keyboard(project_id))
        return
    if not entries:
        await query.edit_message_text(
            "❌ لم أجد أي ملفات Python داخل هذا المشروع. ارفع ملف .py منفرد أو ZIP يحتوي على ملفات .py.",
            reply_markup=project_action_keyboard(project_id),
        )
        return
    context = getattr(query, "_entry_candidates", None)
    ENTRY_CANDIDATES[project_id] = entries
    text = (message_prefix + "\n\n" if message_prefix else "") + "🎯 اختر ملف التشغيل من القائمة التالية:"
    await query.edit_message_text(text, reply_markup=entry_selection_keyboard(project_id, entries))



def dependency_file_candidates(project_dir: Path) -> List[str]:
    """Return likely Python dependency manifest files inside an extracted project."""
    ignored_dirs = {".git", "__pycache__", "venv", ".venv", "env", "node_modules", "site-packages"}
    names = {"requirements.txt", "pyproject.toml", "Pipfile"}
    candidates = []
    for path in project_dir.rglob("*"):
        rel = path.relative_to(project_dir)
        if path.is_file() and path.name in names and not any(part in ignored_dirs for part in rel.parts):
            candidates.append(str(rel).replace(os.sep, "/"))
    candidates.sort(key=lambda x: (Path(x).name != "requirements.txt", len(Path(x).parts), x.lower()))
    return candidates[:40]


def dependency_selection_keyboard(project_id: str, deps: List[str]) -> InlineKeyboardMarkup:
    rows = []
    for idx, dep in enumerate(deps[:30]):
        label = dep if len(dep) <= 45 else "…" + dep[-44:]
        rows.append([InlineKeyboardButton(f"📦 {label}", callback_data=f"deps:{project_id}:{idx}")])
    rows.append([InlineKeyboardButton("🧹 بدون تثبيت مكتبات", callback_data=f"deps:{project_id}:none")])
    rows.append([InlineKeyboardButton("⬅️ رجوع للمشروع", callback_data=f"proj:{project_id}")])
    return InlineKeyboardMarkup(rows)


async def show_dependency_selector(query, project_id: str, manager, message_prefix: str = "") -> None:
    project = bot.registry.get(project_id)
    if not project:
        await query.edit_message_text(t("project_not_found"))
        return
    try:
        project_dir = await bot.project_manager.ensure_project_files(project, manager)
        deps = dependency_file_candidates(project_dir)
    except Exception as exc:
        logger.exception("Failed to prepare dependency selector")
        await query.edit_message_text(t("operation_failed", error=exc), reply_markup=project_action_keyboard(project_id))
        return
    if not deps:
        await query.edit_message_text(
            "⚠️ لم أجد requirements.txt أو pyproject.toml أو Pipfile داخل المشروع. يمكنك رفع ZIP يحتوي ملف المكتبات أو تشغيل المشروع بدون تثبيت.",
            reply_markup=dependency_selection_keyboard(project_id, []),
        )
        return
    DEPENDENCY_CANDIDATES[project_id] = deps
    text = (message_prefix + "\n\n" if message_prefix else "") + "📦 اختر ملف تنزيل المكتبات الذي سيستخدمه pip قبل التشغيل:"
    await query.edit_message_text(text, reply_markup=dependency_selection_keyboard(project_id, deps))

def confirm_keyboard(action: str, project_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data=f"act:{action}:{project_id}"),
         InlineKeyboardButton("❌ إلغاء", callback_data=(f"proj:{project_id}" if project_id != "_" else "menu:system"))],
    ])


# ──────────────────────────────────────────────────────────────────────────
# Command handlers (entry points only — everything else happens via buttons)
# ──────────────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner = is_owner(update)
    welcome_text = (
        "⚡ *ZEUS Uptime* ⚡\n\n"
        "مرحباً بك! أنا بوت متكامل لرفع الملفات إلى Google Drive وإدارة المشاريع المستضافة.\n"
        "استخدم الأزرار الملونة أدناه للتنقل بسهولة بين الخدمات. 🚀"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode='Markdown',
        reply_markup=main_menu_keyboard(owner),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("القائمة الرئيسية 👇", reply_markup=main_menu_keyboard(is_owner(update)))


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(t("privacy"))


# ---- uploads (text commands kept minimal; main flow is drag & drop files) ----

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    document = update.message.document
    if not document:
        return
    if document.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(t("file_too_large", max_gb=MAX_FILE_SIZE // (1024 ** 3)))
        return

    task = UploadTask(
        user_id=user_id,
        file_id=document.file_id,
        file_name=document.file_name or f"document_{document.file_id}",
        file_size=document.file_size,
        message_id=update.message.message_id,
    )
    status_msg = await update.message.reply_text(t("download_started", file_name=task.file_name))
    file_path = await bot.download_telegram_file(task, context)
    if not file_path:
        await status_msg.edit_text(t("download_failed"))
        return
    try:
        lower_name = task.file_name.lower()
        if is_owner(update) and (lower_name.endswith(".zip") or lower_name.endswith(".py")):
            if lower_name.endswith(".zip"):
                project = await bot.register_project_archive(user_id, file_path, task.file_name)
            else:
                project = await bot.register_python_file_project(user_id, file_path, task.file_name)
            entry_text = project.main_entry_file or "غير مكتشف — استخدم زر اختيار ملف التشغيل"
            await status_msg.edit_text(
                t("project_registered", name=project.project_name, id=project.project_id,
                  type=project.project_type, entry=entry_text),
                reply_markup=project_action_keyboard(project.project_id),
            )
        else:
            if is_owner(update):
                await status_msg.edit_text("❌ يدعم رفع المشاريع فقط بصيغة ZIP أو ملف Python منفرد .py")
                return
            success = await (bot.upload_file_chunked(task, file_path)
                              if task.file_size > 20 * 1024 * 1024
                              else bot.upload_file_direct(task, file_path))
            if success:
                await status_msg.edit_text(
                    t("upload_completed"),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                        t("view_drive_button"), url=f"https://drive.google.com/file/d/{task.drive_file_id}/view")]])
                )
            else:
                await status_msg.edit_text(t("upload_failed", error=task.error))
    except Exception as exc:
        logger.exception("Document handling failed")
        await status_msg.edit_text(t("upload_failed", error=exc))
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    if photo.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(t("file_too_large", max_gb=MAX_FILE_SIZE // (1024 ** 3)))
        return
    task = UploadTask(
        user_id=user_id, file_id=photo.file_id, file_name=f"photo_{photo.file_id}.jpg",
        file_size=photo.file_size, message_id=update.message.message_id,
    )
    status_msg = await update.message.reply_text(t("download_started", file_name=task.file_name))
    file_path = await bot.download_telegram_file(task, context)
    if not file_path:
        await status_msg.edit_text(t("download_failed"))
        return
    success = await (bot.upload_file_chunked(task, file_path)
                      if task.file_size > 20 * 1024 * 1024 else bot.upload_file_direct(task, file_path))
    await status_msg.edit_text(t("upload_completed") if success else t("upload_failed", error=task.error))
    if os.path.exists(file_path):
        os.remove(file_path)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    video = update.message.video
    if video.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(t("file_too_large", max_gb=MAX_FILE_SIZE // (1024 ** 3)))
        return
    task = UploadTask(
        user_id=user_id, file_id=video.file_id, file_name=video.file_name or f"video_{video.file_id}.mp4",
        file_size=video.file_size, message_id=update.message.message_id,
    )
    status_msg = await update.message.reply_text(t("download_started", file_name=task.file_name))
    file_path = await bot.download_telegram_file(task, context)
    if not file_path:
        await status_msg.edit_text(t("download_failed"))
        return
    success = await (bot.upload_file_chunked(task, file_path)
                      if task.file_size > 20 * 1024 * 1024 else bot.upload_file_direct(task, file_path))
    await status_msg.edit_text(t("upload_completed") if success else t("upload_failed", error=task.error))
    if os.path.exists(file_path):
        os.remove(file_path)


# ──────────────────────────────────────────────────────────────────────────
# Single callback-query router — this replaces ~20 argument-based commands
# ──────────────────────────────────────────────────────────────────────────

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    await query.answer()

    parts = data.split(":")
    kind = parts[0]
    user_id = query.from_user.id
    manager = bot.get_drive_manager(user_id)

    try:
        # ---------- menus ----------
        if kind == "menu":
            name = parts[1]
            if name == "main":
                await query.edit_message_text("القائمة الرئيسية 👇", reply_markup=main_menu_keyboard(is_owner_id(user_id)))
            elif name == "drive":
                await query.edit_message_text("☁️ قائمة Google Drive 👇", reply_markup=drive_menu_keyboard())
            elif name == "system":
                if not await require_owner_cb(query):
                    return
                await query.edit_message_text("🛰️ لوحة التحكم بالنظام 👇", reply_markup=system_menu_keyboard())
            elif name == "projects":
                if not await require_owner_cb(query):
                    return
                page = int(parts[2]) if len(parts) > 2 else 0
                projects = bot.registry.list_projects()
                header = t("projects_header") if projects else t("no_projects")
                await query.edit_message_text(header, reply_markup=projects_list_keyboard(page))
            elif name == "privacy":
                await query.edit_message_text(t("privacy"), reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ رجوع", callback_data="menu:main")]]))
            return

        # ---------- open a single project's action menu ----------
        if kind == "proj":
            if not await require_owner_cb(query):
                return
            project_id = parts[1]
            project = bot.registry.get(project_id)
            if not project:
                await query.edit_message_text(t("project_not_found"))
                return
            await query.edit_message_text(
                t("project_info", name=project.project_name, id=project.project_id, type=project.project_type,
                  entry=project.main_entry_file or "غير محدد", dependency=project.dependency_file or "تلقائي/غير محدد",
                  env_count=len(project.environment_vars or {}), status=project.status, auto_restart=project.auto_restart,
                  drive_file_id=project.drive_file_id),
                reply_markup=project_action_keyboard(project.project_id),
            )
            return

        # ---------- choose a Python entry file interactively ----------
        if kind == "entry":
            if not await require_owner_cb(query):
                return
            project_id = parts[1]
            idx = int(parts[2]) if len(parts) > 2 else -1
            entries = ENTRY_CANDIDATES.get(project_id, [])
            if idx < 0 or idx >= len(entries):
                await show_entry_selector(query, project_id, manager, "⚠️ انتهت صلاحية قائمة الملفات، اختر مرة أخرى.")
                return
            entry_file = entries[idx]
            project = bot.registry.get(project_id)
            if not project:
                await query.edit_message_text(t("project_not_found"))
                return
            project.main_entry_file = entry_file
            project.startup_command = ["python", entry_file]
            bot.registry.save(project, manager)
            await query.edit_message_text(
                f"✅ تم تعيين ملف التشغيل:\n`{entry_file}`\n\nيمكنك الآن تشغيل المشروع.",
                parse_mode='Markdown',
                reply_markup=project_action_keyboard(project_id),
            )
            return

        # ---------- choose a Python dependency file interactively ----------
        if kind == "deps":
            if not await require_owner_cb(query):
                return
            project_id = parts[1]
            choice = parts[2] if len(parts) > 2 else ""
            project = bot.registry.get(project_id)
            if not project:
                await query.edit_message_text(t("project_not_found"))
                return
            if choice == "none":
                project.dependency_file = None
                bot.registry.save(project, manager)
                await query.edit_message_text("✅ تم تعطيل تثبيت مكتبات Python لهذا المشروع.", reply_markup=project_action_keyboard(project_id))
                return
            idx = int(choice) if choice.isdigit() else -1
            deps = DEPENDENCY_CANDIDATES.get(project_id, [])
            if idx < 0 or idx >= len(deps):
                await show_dependency_selector(query, project_id, manager, "⚠️ انتهت صلاحية قائمة ملفات المكتبات، اختر مرة أخرى.")
                return
            project.dependency_file = deps[idx]
            bot.registry.save(project, manager)
            await query.edit_message_text(
                f"✅ تم تعيين ملف المكتبات:\n`{project.dependency_file}`\n\nسيتم تثبيته تلقائيًا قبل تشغيل المشروع.",
                parse_mode='Markdown',
                reply_markup=project_action_keyboard(project_id),
            )
            return

        # ---------- confirmation gate for destructive actions ----------
        if kind == "conf":
            if not await require_owner_cb(query):
                return
            action, project_id = parts[1], parts[2]
            label = {
                "delete": "حذف المشروع من القائمة فقط",
                "delete_drive": "حذف المشروع نهائيًا من القائمة و Google Drive",
                "stop_all": "إيقاف كل المشاريع",
                "restart_all": "إعادة تشغيل كل المشاريع",
            }.get(action, action)
            await query.edit_message_text(
                f"⚠️ هل أنت متأكد من: {label}؟",
                reply_markup=confirm_keyboard(action, project_id),
            )
            return

        # ---------- actual actions ----------
        if kind == "act":
            if len(parts) < 3:
                return
            action, project_id = parts[1], parts[2]

            # Drive-related (any user)
            if action == "login":
                auth_url = await bot.get_auth_url(user_id)
                text = t("login_message", auth_url=auth_url) if auth_url else t("login_failed")
                await query.edit_message_text(text, reply_markup=drive_menu_keyboard())
                return
            if action == "stat":
                info = await bot.get_storage_info(user_id)
                if not info:
                    await query.edit_message_text(t("auth_required"), reply_markup=drive_menu_keyboard())
                    return
                total = int(info.get('limit', 0))
                used = int(info.get('usage', 0))
                free = total - used
                percent = (used / total * 100) if total > 0 else 0
                await query.edit_message_text(
                    t("storage_info", total=total / (1024 ** 3), used=used / (1024 ** 3),
                      free=free / (1024 ** 3), percent=percent),
                    reply_markup=drive_menu_keyboard(),
                )
                return
            if action == "list":
                files = await bot.list_recent_files(user_id)
                if not files:
                    await query.edit_message_text(t("auth_required"), reply_markup=drive_menu_keyboard())
                    return
                message = t("recent_files_header")
                for i, f in enumerate(files[:10], 1):
                    size_mb = int(f.get('size', 0)) / (1024 ** 2)
                    created = f.get('createdTime', 'غير معروف')
                    message += f"{i}. {f['name']}\n   📏 {size_mb:.1f} MB | 🆔 {f['id']}\n   📅 {created[:10]}\n\n"
                await query.edit_message_text(message, reply_markup=drive_menu_keyboard())
                return
            if action == "upload_help":
                await query.edit_message_text(t("upload_instruction"), reply_markup=drive_menu_keyboard())
                return

            # Everything below is owner-only project / system control
            if not await require_owner_cb(query):
                return

            if action == "status":
                lines = bot.project_manager.status_lines()
                await query.edit_message_text(t("status_header", lines=("\n".join(lines) if lines else t("no_running"))), reply_markup=system_menu_keyboard())
            elif action == "health":
                lines = bot.project_manager.health_lines(manager)
                await query.edit_message_text(t("health_header", lines="\n".join(lines)), reply_markup=system_menu_keyboard())
            elif action == "resources":
                lines = bot.project_manager.resource_lines()
                await query.edit_message_text(t("resources_header", lines=("\n".join(lines) if lines else t("no_running"))), reply_markup=system_menu_keyboard())
            elif action == "uptime":
                lines = bot.project_manager.uptime_lines()
                await query.edit_message_text(t("uptime_header", lines=("\n".join(lines) if lines else t("no_running"))), reply_markup=system_menu_keyboard())
            elif action == "recover":
                count = await bot.project_manager.recover(manager)
                await query.edit_message_text(t("recover_done", count=count), reply_markup=system_menu_keyboard())
            elif action == "backup":
                if not manager.service:
                    await query.edit_message_text(t("auth_required"), reply_markup=system_menu_keyboard())
                    return
                bot.registry.backup_to_drive(manager)
                bot.runtime_store.save(bot.project_manager.running_state, manager)
                bot.log_store.archive_all([p.project_id for p in bot.registry.list_projects()], manager)
                await query.edit_message_text(t("backup_done"), reply_markup=system_menu_keyboard())
            elif action == "stop_all":
                count = await bot.project_manager.stop_all(manager)
                await query.edit_message_text(t("all_stopped", count=count), reply_markup=system_menu_keyboard())
            elif action == "restart_all":
                count = await bot.project_manager.restart_all(manager)
                await query.edit_message_text(t("all_restarted", count=count), reply_markup=system_menu_keyboard())

            elif action == "choose_entry":
                await show_entry_selector(query, project_id, manager)

            elif action == "choose_deps":
                await show_dependency_selector(query, project_id, manager)

            elif action == "start":
                project = bot.registry.get(project_id)
                if project and not project.startup_command:
                    await show_entry_selector(query, project_id, manager, "⚠️ لم أستطع اكتشاف ملف التشغيل تلقائيًا.")
                    return
                try:
                    result = await bot.project_manager.start_project(project_id, manager, auto_restart=True)
                    await query.edit_message_text(t("project_started", result=result), reply_markup=project_action_keyboard(project_id))
                except Exception as exc:
                    if "لا يوجد أمر تشغيل" in str(exc) or "startup" in str(exc).lower():
                        await show_entry_selector(query, project_id, manager, "⚠️ لم أستطع اكتشاف ملف التشغيل تلقائيًا.")
                        return
                    raise
            elif action == "stop":
                result = await bot.project_manager.stop_project(project_id, manager)
                await query.edit_message_text(t("project_started", result=result), reply_markup=project_action_keyboard(project_id))
            elif action == "restart":
                project = bot.registry.get(project_id)
                if project and not project.startup_command:
                    await show_entry_selector(query, project_id, manager, "⚠️ لم أستطع اكتشاف ملف التشغيل تلقائيًا.")
                    return
                try:
                    result = await bot.project_manager.restart_project(project_id, manager)
                    await query.edit_message_text(t("project_started", result=result), reply_markup=project_action_keyboard(project_id))
                except Exception as exc:
                    if "لا يوجد أمر تشغيل" in str(exc) or "startup" in str(exc).lower():
                        await show_entry_selector(query, project_id, manager, "⚠️ لم أستطع اكتشاف ملف التشغيل تلقائيًا.")
                        return
                    raise
            elif action == "info":
                project = bot.registry.get(project_id)
                if not project:
                    await query.edit_message_text(t("project_not_found"))
                    return
                await query.edit_message_text(
                    t("project_info", name=project.project_name, id=project.project_id, type=project.project_type,
                      entry=project.main_entry_file or "غير محدد", dependency=project.dependency_file or "تلقائي/غير محدد",
                      env_count=len(project.environment_vars or {}), status=project.status, auto_restart=project.auto_restart,
                      drive_file_id=project.drive_file_id),
                    reply_markup=project_action_keyboard(project_id),
                )
            elif action == "logs":
                text = bot.log_store.tail(project_id, 100)
                await query.edit_message_text(f"```\n{text[-3500:]}\n```", reply_markup=project_action_keyboard(project_id))
            elif action == "envadd":
                context.user_data["awaiting_env_project_id"] = project_id
                await query.edit_message_text(
                    "➕ أرسل المتغير الآن برسالة نصية بالشكل `KEY=VALUE`.\nمثال: `TOKEN=12345`\n\nملاحظة: يُحفظ في سجل المشروع ويُمرّر للعملية عند التشغيل، وليس متغيرًا عامًا دائمًا على نظام البوت.",
                    parse_mode='Markdown',
                    reply_markup=project_action_keyboard(project_id),
                )
            elif action == "envdel":
                key = parts[3] if len(parts) > 3 else ""
                project = bot.registry.get(project_id)
                if project and key in project.environment_vars:
                    project.environment_vars.pop(key, None)
                    bot.registry.save(project, manager)
                await query.edit_message_text(f"✅ تم حذف المتغير `{key}`.", parse_mode='Markdown', reply_markup=project_action_keyboard(project_id))
            elif action == "env":
                project = bot.registry.get(project_id)
                if not project:
                    await query.edit_message_text(t("project_not_found"))
                    return
                env_vars = project.environment_vars or {}
                rows = []
                for key, value in sorted(env_vars.items()):
                    rows.append([InlineKeyboardButton(f"❌ {key}", callback_data=f"act:envdel:{project_id}:{key}")])
                rows.append([InlineKeyboardButton("➕ إضافة متغير", callback_data=f"act:envadd:{project_id}")])
                rows.append([InlineKeyboardButton("↩️ العودة", callback_data=f"act:info:{project_id}")])
                reply_markup = InlineKeyboardMarkup(rows)
                var_list = "\n".join(f"{k}: `{v}`" for k, v in sorted(env_vars.items()))
                await query.edit_message_text(
                    f"**متغيرات بيئة المشروع {project.project_name}:**\n\n" +
                    (var_list if var_list else "لا توجد متغيرات بعد."),
                    parse_mode='Markdown',
                    reply_markup=reply_markup,
                )
            elif action == "delete":
                await bot.project_manager.stop_project(project_id, manager)
                project = bot.registry.delete(project_id, manager)
                await query.edit_message_text(t("project_deleted") if project else t("project_not_found"),
                                               reply_markup=projects_list_keyboard(0))
            elif action == "delete_drive":
                await bot.project_manager.stop_project(project_id, manager)
                project = bot.registry.delete(project_id, manager)
                if project:
                    manager.delete_file(project.drive_file_id)
                await query.edit_message_text(t("project_deleted") if project else t("project_not_found"),
                                               reply_markup=projects_list_keyboard(0))
    except Exception as exc:
        logger.exception("Callback handling failed")
        try:
            await query.edit_message_text(t("operation_failed", error=exc))
        except Exception:
            pass


def is_owner_id(user_id: int) -> bool:
    return not OWNER_USER_ID or str(user_id) == str(OWNER_USER_ID)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")


async def post_init_recovery(application: Application):
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



async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle one-shot text prompts such as adding project environment variables."""
    if not await require_owner(update):
        return
    project_id = context.user_data.pop("awaiting_env_project_id", None)
    if not project_id:
        return
    text = (update.message.text or "").strip()
    if "=" not in text:
        await update.message.reply_text("❌ الصيغة غير صحيحة. أعد الضغط على إضافة متغير وأرسلها بالشكل KEY=VALUE.")
        return
    key, value = text.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
        await update.message.reply_text("❌ اسم المتغير غير صالح. استخدم حروفًا وأرقامًا وشرطة سفلية فقط، ولا يبدأ برقم.")
        return
    project = bot.registry.get(project_id)
    if not project:
        await update.message.reply_text(t("project_not_found"))
        return
    manager = bot.get_drive_manager(update.effective_user.id)
    project.environment_vars[key] = value
    bot.registry.save(project, manager)
    await update.message.reply_text(
        f"✅ تم حفظ المتغير `{key}` للمشروع `{project.project_name}`. سيُطبّق عند التشغيل/إعادة التشغيل التالية.",
        parse_mode='Markdown',
        reply_markup=project_action_keyboard(project_id),
    )

def start_bot():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN غير موجود في متغيرات البيئة")
        return

    application = Application.builder().token(BOT_TOKEN).post_init(post_init_recovery).build()

    # Slim command surface — navigation now happens through buttons.
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("privacy", privacy_command))

    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_error_handler(error_handler)

    logger.info("بدء تشغيل ZEUS Uptime Bot...")
    application.run_polling()


if __name__ == "__main__":
    start_bot()