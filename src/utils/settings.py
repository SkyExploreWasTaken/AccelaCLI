import logging
import os
import json
import threading
from pathlib import Path
from typing import Any, Tuple

logger = logging.getLogger(__name__)

APP_NAME = "AccelaCLI"
ORG_NAME = "Tachibana Labs"

_settings_local = threading.local()


class RobustSettings:
    """Small JSON-backed settings store with the legacy settings API."""

    def value(self, key: str, defaultValue: Any = None, type: Any = None) -> Any:
        val = self._values.get(key, defaultValue)
        if val is None:
            return defaultValue
        if type is bool or (type is None and isinstance(defaultValue, bool)):
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes")
            return bool(val)
        if type is not None and type is str:
            return str(val)
        return val

    def setValue(self, key: str, value: Any) -> None:
        self._values[key] = value

    def sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._values, indent=2, sort_keys=True), encoding="utf-8")

    def fileName(self) -> str:
        return str(self._path)

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            self._values = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._values = {}


def get_settings() -> RobustSettings:
    """Get the application settings object.

    Returns a thread-local cached instance so background threads do not share
    mutable settings state.
    """
    if not hasattr(_settings_local, "instance"):
        settings_path = _settings_path()
        _settings_local.instance = RobustSettings(settings_path)
        # First run preset overrides for AccelaCLI
        if _settings_local.instance.value("assela_initialized") is None:
            _settings_local.instance.setValue("assela_initialized", True)
            _settings_local.instance.setValue("accent_color", "#a1c9fd")
            _settings_local.instance.setValue("background_color", "#111318")
            _settings_local.instance.setValue("material_preset", "ocean")
            _settings_local.instance.setValue("user_accent_color", "#a1c9fd")
            _settings_local.instance.setValue("user_background_color", "#111318")
            _settings_local.instance.setValue("workshop_steam_enabled", True)
            _settings_local.instance.setValue("workshop_max_downloads", 4)
            _settings_local.instance.setValue("use_lancache", True)
            _settings_local.instance.setValue("prompt_steam_restart", False)
            _settings_local.instance.setValue("sls_config_management", True)
            _settings_local.instance.setValue("font", "Open Sans")
            _settings_local.instance.setValue("font-size", 10)
            _settings_local.instance.setValue("font-style", "Normal")
            _settings_local.instance.sync()

        # Sanitize legacy font settings (e.g. old Accela Trixie Cyrillic typewriter font)
        try:
            saved_font = _settings_local.instance.value("font", "", type=str)
            if saved_font.lower() in ("trixiecyrg-plain", "trixiecyrg-plain regular", "trixie"):
                _settings_local.instance.setValue("font", "Open Sans")
                _settings_local.instance.sync()
            saved_font_file = _settings_local.instance.value("font-file", "", type=str)
            if "trixie" in saved_font_file.lower():
                _settings_local.instance.setValue("font-file", "")
                _settings_local.instance.sync()
        except Exception:
            pass

        # Sanitize default_download_directory (e.g. if transient /tmp was left behind)
        try:
            from utils.paths import is_valid_download_directory
            cur_dl = _settings_local.instance.value("default_download_directory", "", type=str)
            if cur_dl and not is_valid_download_directory(cur_dl):
                _settings_local.instance.setValue("default_download_directory", "")
                _settings_local.instance.sync()
        except Exception:
            pass

        # Tighten permissions because settings can contain API credentials.
        try:
            path = getattr(_settings_local.instance, "fileName", lambda: "")()
            if path:
                p = Path(path)
                if p.exists() and (p.stat().st_mode & 0o077):
                    os.chmod(p, 0o600)
                    logger.info(f"Restricted settings file permissions to 0600: {p}")
        except Exception:
            pass

        # Halloween theme seasonal auto-activation (Oct 26 - Nov 5, one-time only)
        try:
            check_and_apply_halloween_theme(_settings_local.instance)
        except Exception:
            pass

    return _settings_local.instance


def _settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME / "settings.json"
    return Path.home() / ".config" / APP_NAME / "settings.json"


def preserve_branch_selection(settings: Any, appid: Any) -> Tuple[str, str]:
    """Keep the branch the user already selected for a game.

    ``core.morrenus_api.get_selected_branch`` documents the invariant that a
    game installed on a beta branch must never be silently refreshed from the
    public branch, so an existing value is never overwritten. Only a game that
    never had a branch selection at all gets the "public" default.

    Returns the effective ``(selected_branch, installed_branch)`` pair for the
    caller's in-memory game data: a missing side falls back to the other one
    for display, but nothing is written except the "public" default.
    """
    appid_str = str(appid)
    selected = str(settings.value(f"selected_branch/{appid_str}", "", type=str) or "").strip()
    installed = str(settings.value(f"installed_branch/{appid_str}", "", type=str) or "").strip()
    if not selected and not installed:
        settings.setValue(f"selected_branch/{appid_str}", "public")
        settings.setValue(f"installed_branch/{appid_str}", "public")
        return "public", "public"
    return selected or installed, installed or selected


def check_and_apply_halloween_theme(settings: Any) -> bool:
    """Auto-activates Halloween theme once if launched between Oct 26 and Nov 5.
    
    The switch 'halloween_auto_applied' is persistently flipped to True so it never activates
    automatically again, even if the user changes or keeps the theme.
    """
    try:
        from datetime import date
        today = date.today()
        # Active window: October 26 through November 5 (inclusive)
        in_halloween_window = (today.month == 10 and today.day >= 26) or (today.month == 11 and today.day <= 5)
        if in_halloween_window:
            already_applied = settings.value("halloween_auto_applied", False, type=bool)
            if not already_applied:
                settings.setValue("halloween_auto_applied", True)
                settings.setValue("material_preset", "halloween")
                settings.setValue("accent_color", "#ffb77d")
                settings.setValue("background_color", "#141211")
                settings.setValue("user_accent_color", "#ffb77d")
                settings.setValue("user_background_color", "#141211")
                settings.sync()
                return True
    except Exception:
        pass
    return False


def is_canary_welcome_needed() -> bool:
    """True if the Canary Welcome Slideshow should be shown (first Canary launch / onboarding)."""
    s = get_settings()
    return not s.value("canary_welcome_seen", False, type=bool)

