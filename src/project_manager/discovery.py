"""Project archive extraction and type discovery."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Dict, List, Optional


def safe_extract_zip(zip_path: str, destination: str) -> None:
    """Extract a zip file while preventing path traversal."""
    dest = Path(destination).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (dest / member.filename).resolve()
            if not str(target).startswith(str(dest)):
                raise ValueError(f"Unsafe zip member path: {member.filename}")
        archive.extractall(dest)


def _find_first(root: Path, names: List[str]) -> Optional[str]:
    for path in root.rglob("*"):
        if path.is_file() and path.name in names:
            return str(path.relative_to(root))
    return None


def discover_project(root_dir: str) -> Dict[str, Optional[str]]:
    """Detect a Python project type and suggested startup command."""
    root = Path(root_dir)
    has = {path.name for path in root.rglob("*") if path.is_file()}

    entry = _find_first(root, ["bot.py", "main.py", "app.py", "run.py"])
    project_type = "generic_python"
    if "Dockerfile" in has:
        project_type = "docker_ready"
    elif "bot.py" in has:
        project_type = "telegram_or_discord_bot"
    elif "app.py" in has:
        project_type = "flask_or_fastapi"
    elif "main.py" in has:
        project_type = "python_application"
    elif "requirements.txt" in has:
        project_type = "python_project"

    startup_command = ["python", entry] if entry else None
    return {
        "project_type": project_type,
        "main_entry_file": entry,
        "startup_command": startup_command,
    }
