"""Project log file helpers with Google Drive archive support."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from config import LOG_ARCHIVE_DRIVE_PREFIX
from translations.ar import t


class ProjectLogStore:
    def __init__(self, log_dir: str):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, project_id: str) -> Path:
        return self.log_dir / f"{project_id}.log"

    def drive_name_for(self, project_id: str) -> str:
        return f"{LOG_ARCHIVE_DRIVE_PREFIX}_{project_id}.log"

    def tail(self, project_id: str, lines: int = 80) -> str:
        path = self.path_for(project_id)
        if not path.exists():
            return t("no_logs_found")
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return "".join(deque(handle, maxlen=lines)) or t("empty_log_file")

    def archive(self, project_id: str, drive_manager) -> bool:
        path = self.path_for(project_id)
        if not path.exists() or not drive_manager or not drive_manager.service:
            return False
        drive_name = self.drive_name_for(project_id)
        file_id = drive_manager.find_file_by_name(drive_name)
        return bool(drive_manager.upsert_file(str(path), drive_name, file_id))

    def archive_all(self, project_ids: list[str], drive_manager) -> int:
        return sum(1 for project_id in project_ids if self.archive(project_id, drive_manager))

    def restore(self, project_id: str, drive_manager) -> bool:
        if not drive_manager or not drive_manager.service:
            return False
        drive_name = self.drive_name_for(project_id)
        file_id = drive_manager.find_file_by_name(drive_name)
        if not file_id:
            return False
        return drive_manager.download_file(file_id, str(self.path_for(project_id)))

    def restore_all(self, project_ids: list[str], drive_manager) -> int:
        return sum(1 for project_id in project_ids if self.restore(project_id, drive_manager))
