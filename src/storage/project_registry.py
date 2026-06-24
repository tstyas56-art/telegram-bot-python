"""Persistent project metadata registry with optional Google Drive backup."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from models import ProjectRecord
from storage.json_store import JsonStore

logger = logging.getLogger(__name__)


class ProjectRegistry:
    """JSON-backed registry of uploaded projects and their Drive archives."""

    def __init__(self, path: str, drive_file_name: str = "projects.json"):
        self.path = Path(path)
        self.drive_file_name = drive_file_name
        self.store = JsonStore(path)
        self.drive_file_id: Optional[str] = None

    def _load_raw(self) -> Dict:
        try:
            raw = self.store.read({"projects": {}, "drive_file_id": self.drive_file_id})
        except Exception as exc:
            logger.exception("projects registry is missing or corrupt; starting with empty registry: %s", exc)
            raw = {"projects": {}, "drive_file_id": self.drive_file_id}
            self.store.write(raw)
        self.drive_file_id = raw.get("drive_file_id") or self.drive_file_id
        return raw

    def list_projects(self) -> List[ProjectRecord]:
        raw = self._load_raw()
        return [ProjectRecord.from_dict(item) for item in raw.get("projects", {}).values()]

    def get(self, project_id: str) -> Optional[ProjectRecord]:
        item = self._load_raw().get("projects", {}).get(project_id)
        return ProjectRecord.from_dict(item) if item else None

    def find_by_name(self, project_name: str) -> Optional[ProjectRecord]:
        normalized = project_name.lower()
        for project in self.list_projects():
            if project.project_name.lower() == normalized:
                return project
        return None

    def save(self, project: ProjectRecord, drive_manager=None) -> None:
        raw = self._load_raw()
        raw.setdefault("projects", {})[project.project_id] = project.to_dict()
        raw["drive_file_id"] = self.drive_file_id
        self.store.write(raw)
        if drive_manager and drive_manager.service:
            self.backup_to_drive(drive_manager)

    def delete(self, project_id: str, drive_manager=None) -> Optional[ProjectRecord]:
        raw = self._load_raw()
        item = raw.setdefault("projects", {}).pop(project_id, None)
        raw["drive_file_id"] = self.drive_file_id
        self.store.write(raw)
        if drive_manager and drive_manager.service:
            self.backup_to_drive(drive_manager)
        return ProjectRecord.from_dict(item) if item else None

    def backup_to_drive(self, drive_manager) -> bool:
        try:
            file_id = drive_manager.upsert_file(str(self.path), self.drive_file_name, self.drive_file_id)
            if file_id and file_id != self.drive_file_id:
                self.drive_file_id = file_id
                raw = self._load_raw()
                raw["drive_file_id"] = file_id
                self.store.write(raw)
            return bool(file_id)
        except Exception as exc:
            logger.exception("Failed to back up project registry to Drive: %s", exc)
            return False

    def update_env_vars(self, project_id: str, env_vars: Dict[str, str], drive_manager=None) -> Optional[ProjectRecord]:
        """Update environment variables for a project."""
        raw = self._load_raw()
        if project_id not in raw.setdefault("projects", {}):
            return None
        project = ProjectRecord.from_dict(raw["projects"][project_id])
        project.environment_vars = env_vars
        raw["projects"][project_id] = project.to_dict()
        raw["drive_file_id"] = self.drive_file_id
        self.store.write(raw)
        if drive_manager and drive_manager.service:
            self.backup_to_drive(drive_manager)
        return project
