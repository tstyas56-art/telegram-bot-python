"""Persistent runtime state with Google Drive backup support."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from models import RuntimeProjectState
from storage.json_store import JsonStore

logger = logging.getLogger(__name__)


class RuntimeStateStore:
    """Track desired running projects and mirror the state file to Drive."""

    def __init__(self, path: str, drive_file_name: str = "runtime.json"):
        self.path = Path(path)
        self.drive_file_name = drive_file_name
        self.store = JsonStore(path)
        self.drive_file_id: Optional[str] = None

    def load(self) -> Dict[str, RuntimeProjectState]:
        raw = self.store.read({"running_projects": {}, "drive_file_id": self.drive_file_id})
        self.drive_file_id = raw.get("drive_file_id") or self.drive_file_id
        return {
            project_id: RuntimeProjectState.from_dict(data)
            for project_id, data in raw.get("running_projects", {}).items()
        }

    def save(self, running: Dict[str, RuntimeProjectState], drive_manager=None) -> None:
        data = {
            "drive_file_id": self.drive_file_id,
            "running_projects": {key: value.to_dict() for key, value in running.items()},
        }
        self.store.write(data)
        if drive_manager and drive_manager.service:
            self.backup_to_drive(drive_manager)

    def backup_to_drive(self, drive_manager) -> None:
        try:
            file_id = drive_manager.upsert_file(str(self.path), self.drive_file_name, self.drive_file_id)
            if file_id and file_id != self.drive_file_id:
                self.drive_file_id = file_id
                data = self.store.read({"running_projects": {}})
                data["drive_file_id"] = file_id
                self.store.write(data)
        except Exception as exc:
            logger.exception("Failed to back up runtime state to Drive: %s", exc)

    def restore_from_drive(self, drive_manager) -> bool:
        try:
            file_id = self.drive_file_id or drive_manager.find_file_by_name(self.drive_file_name)
            if not file_id:
                return False
            self.drive_file_id = file_id
            return drive_manager.download_file(file_id, str(self.path))
        except Exception as exc:
            logger.exception("Failed to restore runtime state from Drive: %s", exc)
            return False
