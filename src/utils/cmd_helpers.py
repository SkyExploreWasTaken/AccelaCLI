"""Headless helpers shared by the CMD application."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def get_base_path(app_name: str = "AccelaCLI") -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path(sys.argv[0]).resolve().parent)) / app_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / app_name


def get_data_dir() -> Path:
    path = get_base_path() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


DB_RELOCATED_FILES = {
    "depot_keys.db", "steam_headers.db", "install_history.json",
    "games.db", "AccelaCLI.db",
}


def get_data_file_path(filename: str) -> Path:
    base = get_base_path()
    if filename in DB_RELOCATED_FILES:
        path = base / "db" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return get_data_dir() / filename


def resource_path(relative_path: str) -> Path:
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(sys.argv[0]))
    return Path(base) / relative_path


def get_dotnet_path() -> str | None:
    return shutil.which("dotnet")


def get_dotnet_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    env.setdefault("DOTNET_SYSTEM_GLOBALIZATION_INVARIANT", "1")
    dotnet = get_dotnet_path()
    if dotnet:
        env["DOTNET_ROOT"] = str(Path(dotnet).resolve().parent)
    return env


def ensure_dotnet_availability() -> bool:
    """Return whether the system .NET runtime is available."""
    dotnet = get_dotnet_path()
    if not dotnet:
        logger.error(".NET runtime was not found on PATH.")
        return False
    try:
        subprocess.run(
            [dotnet, "--list-runtimes"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("Could not verify .NET runtime: %s", exc)
        return False
    return True


