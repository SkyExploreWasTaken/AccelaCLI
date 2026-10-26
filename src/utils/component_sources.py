"""Configurable sources for individual optional-component files."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from utils.cmd_helpers import get_base_path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = get_base_path() / "component_sources.toml"

EXAMPLE_CONFIG = """# AccelaCLI optional-component file sources
# Each entry replaces a file inside the component when its source is available.
# Sources may be HTTPS URLs, local files, or directories containing the named file.
# If a configured source is unavailable, AccelaCLI keeps using the normal R2 archive.
#
# The platform sections are: windows, linux, and macos.
#
# Example:
# [components.goldberg.windows]
# "steam_api64.dll" = "C:\\\\AccelaCLI\\\\components\\\\steam_api64.dll"
# "steam_api.dll" = "https://example.invalid/steam_api.dll"
#
# [components.goldberg.linux]
# "libsteam_api.so" = "https://example.invalid/libsteam_api.so"
# "libsteam_api64.so" = "C:\\\\AccelaCLI\\\\components\\\\libsteam_api64.so"
#
# [components.steamless.windows]
# "steamless.exe" = "D:\\\\tools\\\\steamless.exe"
"""


def config_path(explicit: Path | None = None) -> Path:
    return explicit.expanduser().resolve() if explicit else DEFAULT_CONFIG_PATH


def ensure_config_file(explicit: Path | None = None) -> Path:
    """Create the example config once and return its path."""
    path = config_path(explicit)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    elif not path.is_file():
        raise ValueError(f"Component source config is not a file: {path}")
    return path


def load_sources(
    component: str, platform_name: str, explicit: Path | None = None
) -> dict[str, Any]:
    """Load configured file sources for one component and platform."""
    path = ensure_config_file(explicit)
    try:
        import tomllib

        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read component source config {path}: {exc}") from exc

    components = data.get("components", {})
    if not isinstance(components, dict):
        raise ValueError(f"Invalid component source config {path}: components must be a table")
    component_data = components.get(component, {})
    if not isinstance(component_data, dict):
        raise ValueError(f"Invalid component source config {path}: {component} must be a table")
    platform_data = component_data.get(platform_name, {})
    if not isinstance(platform_data, dict):
        raise ValueError(
            f"Invalid component source config {path}: "
            f"components.{component}.{platform_name} must be a table"
        )
    return platform_data


def current_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _source_value(value: Any) -> tuple[str, str | None]:
    if isinstance(value, str):
        return value, None
    if isinstance(value, dict) and isinstance(value.get("source"), str):
        checksum = value.get("sha256")
        return value["source"], checksum if isinstance(checksum, str) else None
    raise ValueError("source entries must be strings or {source = \"...\", sha256 = \"...\"}")


def apply_overrides(
    component: str,
    staged_dir: Path,
    platform_name: str,
    explicit_config: Path | None = None,
) -> None:
    """Overlay configured files onto an already extracted component archive.

    Custom sources are optional. A failed custom source is logged and leaves the
    corresponding file from the verified R2 archive in place.
    """
    sources = load_sources(component, platform_name, explicit_config)
    for relative_name, raw_source in sources.items():
        if not isinstance(relative_name, str):
            raise ValueError("Component source file names must be strings")
        source, expected_sha = _source_value(raw_source)
        destination_relative = Path(relative_name)
        if (
            component == "goldberg"
            and destination_relative.parts
            and destination_relative.parts[0].lower()
            not in {"windows", "linux", "macos"}
        ):
            destination_relative = Path(platform_name) / destination_relative
        destination = (staged_dir / destination_relative).resolve()
        if staged_dir.resolve() not in destination.parents:
            raise ValueError(f"Unsafe component source file path: {relative_name}")
        try:
            with tempfile.TemporaryDirectory(prefix="AccelaCLI_component_source_") as temp:
                temporary = Path(temp) / Path(relative_name).name
                if source.lower().startswith(("http://", "https://")):
                    request = urllib.request.Request(
                        source, headers={"User-Agent": "AccelaCLI-ComponentManager/1.0"}
                    )
                    with urllib.request.urlopen(request, timeout=120) as response:
                        with temporary.open("wb") as output:
                            shutil.copyfileobj(response, output)
                else:
                    source_path = Path(os.path.expandvars(source)).expanduser()
                    if source_path.is_dir():
                        source_path = source_path / Path(relative_name).name
                    if not source_path.is_file():
                        raise FileNotFoundError(source_path)
                    shutil.copyfile(source_path, temporary)

                if expected_sha:
                    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
                    if digest.lower() != expected_sha.lower():
                        raise ValueError(f"SHA-256 mismatch for {relative_name}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(temporary, destination)
                logger.info(
                    "[ComponentManager] Applied configured source for %s/%s",
                    component,
                    relative_name,
                )
        except (OSError, ValueError, urllib.error.URLError) as exc:
            logger.warning(
                "[ComponentManager] Configured source for %s/%s unavailable; "
                "using the R2 archive copy: %s",
                component,
                relative_name,
                exc,
            )


