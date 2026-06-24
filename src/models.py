"""Shared data models for hosted projects."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProjectVersion:
    version: int
    drive_file_id: str
    uploaded_at: str
    file_name: str


@dataclass
class ProjectRecord:
    project_id: str
    project_name: str
    project_type: str
    drive_file_id: str
    upload_date: str
    main_entry_file: Optional[str]
    status: str = "stopped"
    auto_restart: bool = False
    startup_command: Optional[List[str]] = None
    versions: List[ProjectVersion] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectRecord":
        versions = [ProjectVersion(**item) for item in data.get("versions", [])]
        data = {**data, "versions": versions}
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeProjectState:
    project_id: str
    project_name: str
    startup_command: List[str]
    auto_restart: bool
    last_start_timestamp: str
    status: str = "running"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeProjectState":
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
