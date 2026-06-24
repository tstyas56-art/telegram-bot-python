"""Project log file helpers."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from translations.ar import t


class ProjectLogStore:
    def __init__(self, log_dir: str):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, project_id: str) -> Path:
        return self.log_dir / f"{project_id}.log"

    def tail(self, project_id: str, lines: int = 80) -> str:
        path = self.path_for(project_id)
        if not path.exists():
            return t("no_logs_found")
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return "".join(deque(handle, maxlen=lines)) or t("empty_log_file")
