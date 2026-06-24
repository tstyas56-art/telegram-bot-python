"""Small atomic JSON persistence helper."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


class JsonStore:
    """Read and write JSON files atomically for Railway's ephemeral disk."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.path.exists():
            return default or {}
        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def write(self, data: Dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, self.path)
