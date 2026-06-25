"""Start, stop, monitor, recover, and inspect hosted projects."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


from config import AUTO_RESTART_DELAY_SECONDS, MAX_AUTO_RESTART_ATTEMPTS, MAX_RUNNING_PROJECTS
from logs.project_logs import ProjectLogStore
from models import RuntimeProjectState, utc_now_iso
from project_manager.discovery import safe_extract_zip
from storage.project_registry import ProjectRegistry
from translations.ar import t

logger = logging.getLogger(__name__)


class ProjectManager:
    def __init__(self, registry: ProjectRegistry, runtime_store, workspace_dir: str, log_store: ProjectLogStore, notifier=None):
        self.registry = registry
        self.runtime_store = runtime_store
        self.workspace_dir = Path(workspace_dir)
        self.log_store = log_store
        self.notifier = notifier
        self.processes: Dict[str, asyncio.subprocess.Process] = {}
        self.running_state: Dict[str, RuntimeProjectState] = runtime_store.load()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    async def notify_owner(self, message: str) -> None:
        if self.notifier:
            await self.notifier(message)

    def running_count(self) -> int:
        return sum(1 for process in self.processes.values() if process.returncode is None)

    async def ensure_project_files(self, project, drive_manager, force: bool = False) -> Path:
        project_dir = self.workspace_dir / project.project_id
        if project_dir.exists() and not force:
            return project_dir
        if project_dir.exists():
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.workspace_dir / f"{project.project_id}.zip"
        if not drive_manager.download_file(project.drive_file_id, str(archive_path)):
            await self.notify_owner(t("drive_download_failed", name=project.project_name))
            raise RuntimeError(t("drive_download_failed", name=project.project_name))
        safe_extract_zip(str(archive_path), str(project_dir))
        archive_path.unlink(missing_ok=True)
        return project_dir

    def validate_project_file(self, project_dir: Path, file_path: str) -> str:
        selected = Path(file_path)
        if selected.is_absolute() or ".." in selected.parts:
            raise ValueError("المسار غير آمن أو خارج مجلد المشروع")
        target = (project_dir / selected).resolve()
        if not str(target).startswith(str(project_dir.resolve())) or not target.is_file():
            raise ValueError("الملف غير موجود داخل مجلد المشروع")
        return str(selected).replace(os.sep, "/")

    def _detect_dependency_file(self, project_dir: Path, entry_file: Optional[str] = None) -> Optional[str]:
        names = ["requirements.txt", "pyproject.toml", "Pipfile"]
        search_roots = []
        if entry_file:
            entry_path = (project_dir / entry_file).resolve()
            if entry_path.exists():
                current = entry_path.parent
                while str(current).startswith(str(project_dir.resolve())):
                    search_roots.append(current)
                    if current == project_dir.resolve():
                        break
                    current = current.parent
        search_roots.append(project_dir.resolve())
        for root in search_roots:
            for name in names:
                candidate = root / name
                if candidate.is_file():
                    return str(candidate.relative_to(project_dir.resolve())).replace(os.sep, "/")
        for path in project_dir.rglob("*"):
            if path.is_file() and path.name in names:
                rel = path.relative_to(project_dir)
                if not any(part in {".git", "venv", ".venv", "env", "node_modules", "__pycache__"} for part in rel.parts):
                    return str(rel).replace(os.sep, "/")
        return None

    async def install_requirements(self, project_id: str, project_dir: Path, dependency_file: Optional[str] = None) -> None:
        dependency_rel = dependency_file or self._detect_dependency_file(project_dir)
        if not dependency_rel:
            return
        dependency_rel = self.validate_project_file(project_dir, dependency_rel)
        dependency_path = project_dir / dependency_rel
<<<<<<< HEAD
        if dependency_path.name == "requirements.txt":
            install_command = [sys.executable, "-m", "pip", "install", "-r", str(dependency_path)]
        elif dependency_path.name == "pyproject.toml":
            install_command = [sys.executable, "-m", "pip", "install", str(dependency_path.parent)]
=======
        packages_dir = project_dir / ".python_packages"
        packages_dir.mkdir(exist_ok=True)
        if dependency_path.name == "requirements.txt":
            install_command = [sys.executable, "-m", "pip", "install", "--upgrade", "--target", str(packages_dir), "-r", str(dependency_path)]
        elif dependency_path.name == "pyproject.toml":
            install_command = [sys.executable, "-m", "pip", "install", "--upgrade", "--target", str(packages_dir), str(dependency_path.parent)]
>>>>>>> codex/fix-environment-variable-button-functionality
        elif dependency_path.name == "Pipfile":
            install_command = [sys.executable, "-m", "pip", "install", "pipenv"]
        else:
            return
        log_path = self.log_store.path_for(project_id)
        with log_path.open("ab", buffering=0) as log_file:
            log_file.write(f"\n--- Installing Python dependencies from {dependency_rel} ---\n".encode())
            process = await asyncio.create_subprocess_exec(
                *install_command,
                cwd=str(project_dir),
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            return_code = await process.wait()
            if return_code != 0:
                await self.notify_owner(t("requirements_failed"))
                raise RuntimeError(t("requirements_failed"))

    async def install_dependencies(self, project_id: str, project_dir: Path) -> None:
        """Install Node.js dependencies if package.json exists."""
        package_json = project_dir / "package.json"
        if not package_json.exists():
            return
        log_path = self.log_store.path_for(project_id)
        with log_path.open("ab", buffering=0) as log_file:
            log_file.write(b"\n--- Installing npm dependencies ---\n")
            process = await asyncio.create_subprocess_exec(
                "npm",
                "install",
                cwd=str(project_dir),
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                env=os.environ,
            )
            return_code = await process.wait()
            if return_code != 0:
                await self.notify_owner(t("npm_install_failed"))
                raise RuntimeError(t("npm_install_failed"))


    def validate_entry_file(self, project_dir: Path, entry_file: str) -> str:
        entry = Path(entry_file)
        if entry.is_absolute() or ".." in entry.parts:
            raise ValueError("ملف التشغيل غير آمن أو خارج مجلد المشروع")
        return self.validate_project_file(project_dir, entry_file)
    async def start_project(
        self,
        project_id: str,
        drive_manager,
        auto_restart: Optional[bool] = None,
        entry_file: Optional[str] = None,
        restart_attempts: Optional[int] = None,
        force_download: bool = False,
    ) -> str:
        if project_id in self.processes and self.processes[project_id].returncode is None:
            return t("already_running")
        if self.running_count() >= MAX_RUNNING_PROJECTS:
            raise RuntimeError(t("max_running_reached", limit=MAX_RUNNING_PROJECTS))
        project = self.registry.get(project_id)
        if not project:
            raise ValueError(t("manager_project_not_found"))
        if not project.startup_command and not entry_file:
            raise ValueError(t("no_startup_command"))
        project_dir = await self.ensure_project_files(project, drive_manager, force=force_download)
        if entry_file:
            safe_entry = self.validate_entry_file(project_dir, entry_file)
            project.main_entry_file = safe_entry
            project.startup_command = ["python", safe_entry]
            if not project.dependency_file:
                project.dependency_file = self._detect_dependency_file(project_dir, safe_entry)
        if not project.dependency_file:
            project.dependency_file = self._detect_dependency_file(project_dir, project.main_entry_file)
        await self.install_requirements(project_id, project_dir, project.dependency_file)
        await self.install_dependencies(project_id, project_dir)
        log_path = self.log_store.path_for(project_id)
        log_file = log_path.open("ab", buffering=0)
        # Build environment with project-specific variables
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        packages_dir = project_dir / ".python_packages"
        if packages_dir.exists():
            existing_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = str(packages_dir) if not existing_pythonpath else f"{packages_dir}{os.pathsep}{existing_pythonpath}"
        for key, value in project.environment_vars.items():
            env[key] = value
        # Map command interpreter correctly
        command = []
        for part in project.startup_command:
            if part == "python":
                command.append(sys.executable)
            elif part == "node":
                node_path = shutil.which("node") or "/usr/local/bin/node"
                command.append(node_path)
            elif part == "npm":
                npm_path = shutil.which("npm") or "/usr/local/bin/npm"
                command.append(npm_path)
            else:
                command.append(part)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(project_dir),
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        self.processes[project_id] = process
        project.status = "running"
        if auto_restart is not None:
            project.auto_restart = auto_restart
        self.registry.save(project, drive_manager)
        self.running_state[project_id] = RuntimeProjectState(
            project_id=project.project_id,
            project_name=project.project_name,
            startup_command=project.startup_command,
            auto_restart=project.auto_restart,
            last_start_timestamp=utc_now_iso(),
            restart_attempts=restart_attempts or 0,
        )
        self.runtime_store.save(self.running_state, drive_manager)
        asyncio.create_task(self._monitor(project_id, drive_manager, log_file))
        return t("manager_started", pid=process.pid)

    async def _monitor(self, project_id: str, drive_manager, log_file) -> None:
        process = self.processes[project_id]
        return_code = await process.wait()
        log_file.close()
        self.processes.pop(project_id, None)
        project = self.registry.get(project_id)
        if project:
            project.status = "crashed" if return_code else "stopped"
            self.registry.save(project, drive_manager)
        state = self.running_state.get(project_id)
        if state and state.auto_restart and return_code != 0:
            attempts = state.restart_attempts + 1
            if attempts <= MAX_AUTO_RESTART_ATTEMPTS:
                logger.warning("Project %s crashed with %s; restarting attempt %s", project_id, return_code, attempts)
                await asyncio.sleep(AUTO_RESTART_DELAY_SECONDS)
                await self.start_project(project_id, drive_manager, auto_restart=True, restart_attempts=attempts)
                return
            logger.error("Project %s reached max restart attempts", project_id)
            await self.notify_owner(f"❌ تعطل المشروع {project_id} بعد استنفاد كل محاولات إعادة التشغيل.")
        self.log_store.archive(project_id, drive_manager)
        self.running_state.pop(project_id, None)
        self.runtime_store.save(self.running_state, drive_manager)

    async def stop_project(self, project_id: str, drive_manager) -> str:
        process = self.processes.get(project_id)
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=15)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        self.processes.pop(project_id, None)
        self.running_state.pop(project_id, None)
        project = self.registry.get(project_id)
        if project:
            project.status = "stopped"
            self.registry.save(project, drive_manager)
        self.runtime_store.save(self.running_state, drive_manager)
        self.log_store.archive(project_id, drive_manager)
        return t("manager_stopped")

    async def restart_project(self, project_id: str, drive_manager, entry_file: Optional[str] = None) -> str:
        await self.stop_project(project_id, drive_manager)
        return await self.start_project(project_id, drive_manager, entry_file=entry_file)

    async def recover(self, drive_manager) -> int:
        self.registry.restore_from_drive(drive_manager)
        self.runtime_store.restore_from_drive(drive_manager)
        self.log_store.restore_all([project.project_id for project in self.registry.list_projects()], drive_manager)
        self.running_state = self.runtime_store.load()
        count = 0
        for project_id, state in list(self.running_state.items()):
            try:
                await self.start_project(
                    project_id,
                    drive_manager,
                    auto_restart=state.auto_restart,
                    restart_attempts=state.restart_attempts,
                    force_download=True,
                )
                count += 1
            except Exception as exc:
                logger.exception("Failed to recover project %s", project_id)
                await self.notify_owner(f"❌ فشل استعادة المشروع {project_id}: {exc}")
        return count

    async def stop_all(self, drive_manager) -> int:
        ids = list(self.processes.keys())
        for project_id in ids:
            await self.stop_project(project_id, drive_manager)
        return len(ids)

    async def restart_all(self, drive_manager) -> int:
        ids = list(self.running_state.keys() or self.processes.keys())
        count = 0
        for project_id in ids:
            await self.restart_project(project_id, drive_manager)
            count += 1
        return count

    def status_lines(self) -> list[str]:
        lines = []
        for project in self.registry.list_projects():
            process = self.processes.get(project.project_id)
            pid = process.pid if process and process.returncode is None else "-"
            lines.append(f"{project.project_name} ({project.project_id}) — {project.status} — PID {pid}")
        return lines

    def running_lines(self) -> list[str]:
        return [line for line in self.status_lines() if "PID -" not in line]

    def resource_lines(self) -> list[str]:
        lines = []
        for project_id, process in self.processes.items():
            if process.returncode is not None:
                continue
            try:
                mem_mb = self._memory_mb(process.pid)
                cpu_percent = self._cpu_percent(project_id, process.pid)
                uptime = self._uptime_for(project_id)
                project = self.registry.get(project_id)
                name = project.project_name if project else project_id
                lines.append(f"{name} ({project_id}) — CPU {cpu_percent:.1f}% — RAM {mem_mb:.1f}MB — مدة التشغيل {uptime}")
            except OSError as exc:
                lines.append(t("resource_error", project_id=project_id, error=exc))
        return lines

    def _memory_mb(self, pid: int) -> float:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024
        return 0.0

    def _cpu_percent(self, project_id: str, pid: int) -> float:
        state = self.running_state.get(project_id)
        if not state:
            return 0.0
        started = datetime.fromisoformat(state.last_start_timestamp)
        elapsed = max((datetime.now(timezone.utc) - started).total_seconds(), 1)
        clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as handle:
            parts = handle.read().split()
        cpu_seconds = (float(parts[13]) + float(parts[14])) / clock_ticks
        return min((cpu_seconds / elapsed) * 100, 100.0)

    def uptime_lines(self) -> list[str]:
        lines = []
        for project_id in self.processes:
            project = self.registry.get(project_id)
            name = project.project_name if project else project_id
            lines.append(f"{name} ({project_id}) — {self._uptime_for(project_id)}")
        return lines

    def _uptime_for(self, project_id: str) -> str:
        state = self.running_state.get(project_id)
        if not state:
            return t("unknown")
        started = datetime.fromisoformat(state.last_start_timestamp)
        seconds = int((datetime.now(timezone.utc) - started).total_seconds())
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}س {minutes}د {seconds}ث"

    def health_lines(self, drive_manager) -> list[str]:
        lines = []
        lines.append(t("health_drive_ok") if drive_manager.service else t("health_drive_bad"))
        try:
            self.runtime_store.load()
            lines.append(t("health_runtime_ok"))
        except Exception as exc:
            lines.append(t("health_runtime_bad", error=exc))
        for project in self.registry.list_projects():
            local = self.workspace_dir / project.project_id
            process = self.processes.get(project.project_id)
            proc_ok = process is not None and process.returncode is None
            lines.append(
                t("health_project", name=project.project_name, files_status=(t("health_files_present") if local.exists() else t("health_files_missing")), process_status=(t("health_process_running") if proc_ok else t("health_process_stopped")))
            )
        return lines