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


def _find_dependency_file(root: Path, entry: Optional[str] = None) -> Optional[str]:
    """Find the best Python dependency manifest for the detected entry file."""
    names = ["requirements.txt", "pyproject.toml", "Pipfile"]
    if entry:
        entry_parent = (root / entry).parent
        for current in [entry_parent, *entry_parent.parents]:
            if root not in [current, *current.parents] and current != root:
                continue
            for name in names:
                candidate = current / name
                if candidate.is_file():
                    return str(candidate.relative_to(root))
            if current == root:
                break
    return _find_first(root, names)


def discover_project(root_dir: str) -> Dict[str, Optional[str]]:
    """Detect project type and suggested startup command."""
    root = Path(root_dir)
    has = {path.name for path in root.rglob("*") if path.is_file()}

    entry = None
    project_type = "generic_python"
    startup_command = None

    # Python detection (same as before)
    python_entry = _find_first(root, ["bot.py", "main.py", "app.py", "run.py"])
    if python_entry:
        entry = python_entry
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
        else:
            project_type = "generic_python"
        startup_command = ["python", entry]

    # Node/JavaScript detection
    elif "package.json" in has:
        project_type = "nodejs"
        # Look for common entry points
        node_entry = _find_first(root, ["index.js", "server.js", "app.js", "main.js"])
        entry = node_entry
        if entry:
            startup_command = ["node", entry]
        else:
            startup_command = ["npm", "start"]

    # Generic JavaScript (no package.json)
    elif "index.js" in has or "server.js" in has:
        project_type = "javascript"
        entry = _find_first(root, ["index.js", "server.js", "app.js", "main.js"])
        if entry:
            startup_command = ["node", entry]

    # Generic script detection (shell)
    elif "start.sh" in has or "run.sh" in has:
        project_type = "shell_script"
        entry = _find_first(root, ["start.sh", "run.sh"])
        startup_command = ["sh", entry] if entry else None

    # Fallback: treat as unknown but keep it in registry
    else:
        project_type = "unknown"
        entry = None
        startup_command = None

    return {
        "project_type": project_type,
        "main_entry_file": entry,
        "startup_command": startup_command,
        "dependency_file": _find_dependency_file(root, entry) if project_type not in {"nodejs", "javascript", "shell_script"} else None,
    }
