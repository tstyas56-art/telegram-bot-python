"""Start, stop, monitor, and recover hosted projects."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

from logs.project_logs import ProjectLogStore
from models import RuntimeProjectState, utc_now_iso
from project_manager.discovery import safe_extract_zip
from storage.project_registry import ProjectRegistry

logger = logging.getLogger(__name__)


class ProjectManager:
    def __init__(self, registry: ProjectRegistry, runtime_store, workspace_dir: str, log_store: ProjectLogStore):
        self.registry = registry
        self.runtime_store = runtime_store
        self.workspace_dir = Path(workspace_dir)
        self.log_store = log_store
        self.processes: Dict[str, asyncio.subprocess.Process] = {}
        self.running_state: Dict[str, RuntimeProjectState] = runtime_store.load()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    async def ensure_project_files(self, project, drive_manager) -> Path:
        project_dir = self.workspace_dir / project.project_id
        if project_dir.exists():
            return project_dir
        project_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.workspace_dir / f"{project.project_id}.zip"
        if not drive_manager.download_file(project.drive_file_id, str(archive_path)):
            raise RuntimeError(f"Failed to download {project.project_name} from Drive")
        safe_extract_zip(str(archive_path), str(project_dir))
        archive_path.unlink(missing_ok=True)
        return project_dir

    async def start_project(self, project_id: str, drive_manager, auto_restart: Optional[bool] = None) -> str:
        if project_id in self.processes and self.processes[project_id].returncode is None:
            return "already running"
        project = self.registry.get(project_id)
        if not project:
            raise ValueError("Project not found")
        if not project.startup_command:
            raise ValueError("Project has no detected startup command")
        project_dir = await self.ensure_project_files(project, drive_manager)
        log_path = self.log_store.path_for(project_id)
        log_file = log_path.open("ab", buffering=0)
        command = [sys.executable if part == "python" else part for part in project.startup_command]
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(project_dir),
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        self.processes[project_id] = process
        project.status = "running"
        if auto_restart is not None:
            project.auto_restart = auto_restart
        self.registry.save(project)
        self.running_state[project_id] = RuntimeProjectState(
            project_id=project.project_id,
            project_name=project.project_name,
            startup_command=project.startup_command,
            auto_restart=project.auto_restart,
            last_start_timestamp=utc_now_iso(),
        )
        self.runtime_store.save(self.running_state, drive_manager)
        asyncio.create_task(self._monitor(project_id, drive_manager, log_file))
        return f"started pid={process.pid}"

    async def _monitor(self, project_id: str, drive_manager, log_file) -> None:
        process = self.processes[project_id]
        return_code = await process.wait()
        log_file.close()
        project = self.registry.get(project_id)
        if project:
            project.status = "crashed" if return_code else "stopped"
            self.registry.save(project)
        state = self.running_state.get(project_id)
        if state and state.auto_restart:
            logger.warning("Project %s exited with %s; auto-restarting", project_id, return_code)
            await asyncio.sleep(3)
            await self.start_project(project_id, drive_manager, auto_restart=True)
            return
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
            self.registry.save(project)
        self.runtime_store.save(self.running_state, drive_manager)
        return "stopped"

    async def restart_project(self, project_id: str, drive_manager) -> str:
        await self.stop_project(project_id, drive_manager)
        return await self.start_project(project_id, drive_manager)

    async def recover(self, drive_manager) -> int:
        self.runtime_store.restore_from_drive(drive_manager)
        self.running_state = self.runtime_store.load()
        count = 0
        for project_id, state in list(self.running_state.items()):
            try:
                await self.start_project(project_id, drive_manager, auto_restart=state.auto_restart)
                count += 1
            except Exception:
                logger.exception("Failed to recover project %s", project_id)
        return count

    def status_lines(self) -> list[str]:
        lines = []
        for project in self.registry.list_projects():
            process = self.processes.get(project.project_id)
            pid = process.pid if process and process.returncode is None else "-"
            lines.append(f"{project.project_name} ({project.project_id}) — {project.status} — pid {pid}")
        return lines
