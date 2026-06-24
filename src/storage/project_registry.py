"""Persistent project metadata registry."""

from __future__ import annotations

from typing import Dict, List, Optional

from models import ProjectRecord
from storage.json_store import JsonStore


class ProjectRegistry:
    """JSON-backed registry of uploaded projects and their Drive archives."""

    def __init__(self, path: str):
        self.store = JsonStore(path)

    def _load_raw(self) -> Dict:
        return self.store.read({"projects": {}})

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

    def save(self, project: ProjectRecord) -> None:
        raw = self._load_raw()
        raw.setdefault("projects", {})[project.project_id] = project.to_dict()
        self.store.write(raw)

    def delete(self, project_id: str) -> Optional[ProjectRecord]:
        raw = self._load_raw()
        item = raw.setdefault("projects", {}).pop(project_id, None)
        self.store.write(raw)
        return ProjectRecord.from_dict(item) if item else None
