"""Headless auto-tuning helpers for DepotDownloader concurrency."""

import logging
import os

logger = logging.getLogger(__name__)

MIN_CONCURRENCY = 2
MAX_CONCURRENCY = 30
STEP = 2
DEFAULT_START = 8
SPEED_EPS_BPS = 1024.0
MIN_WIRE_BYTES_FOR_LEARNING = 1_048_576
IMPROVE_FACTOR = 1.05
DEGRADE_FACTOR = 0.80

_LEGACY_BEST_KEYS = (
    "auto_tune_best_lan", "auto_tune_best_mbps_lan",
    "auto_tune_best_wan", "auto_tune_best_mbps_wan",
    "workshop_auto_tune_best_lan", "workshop_auto_tune_best_mbps_lan",
    "workshop_auto_tune_best_wan", "workshop_auto_tune_best_mbps_wan",
)


def clamp(value: int, low: int, high: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = low
    return max(low, min(high, value))


def read_int(settings, key: str, default: int) -> int:
    try:
        return int(settings.value(key, default, type=int))
    except (TypeError, ValueError, AttributeError):
        return default


def read_bool(settings, key: str, default: bool) -> bool:
    try:
        return bool(settings.value(key, default, type=bool))
    except (TypeError, ValueError, AttributeError):
        return default


def read_float(settings, key: str, default: float) -> float:
    try:
        return float(settings.value(key, default, type=float))
    except (TypeError, ValueError, AttributeError):
        return default


def is_auto_enabled(settings, auto_key: str = "auto_max_downloads") -> bool:
    return settings is not None and read_bool(settings, auto_key, True)


def get_cap(settings, cap_key: str = "max_downloads", default: int = 8) -> int:
    if settings is None:
        return default
    return clamp(read_int(settings, cap_key, default), 1, MAX_CONCURRENCY)


def should_learn(wire_bytes: float) -> bool:
    try:
        return float(wire_bytes) >= MIN_WIRE_BYTES_FOR_LEARNING
    except (TypeError, ValueError):
        return False


def _network_fingerprint() -> str:
    """Return a stable local network identifier when Linux route data exists."""
    try:
        iface = None
        gateway = None
        with open("/proc/net/route", encoding="utf-8") as route_file:
            for line in route_file.read().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 8 and parts[1] == "00000000" and int(parts[3], 16) & 0x2:
                    iface, gateway = parts[0], parts[2]
                    break
        if not iface:
            return "nonet"
        kind = "wl" if os.path.isdir(f"/sys/class/net/{iface}/wireless") else "eth"
        mac_tail = "nogw"
        gateway_ip = ".".join(str(int(gateway[i:i + 2], 16)) for i in (6, 4, 2, 0))
        with open("/proc/net/arp", encoding="utf-8") as arp_file:
            for line in arp_file.read().splitlines()[1:]:
                cells = line.split()
                if len(cells) >= 4 and cells[0] == gateway_ip and len(cells[3]) == 17:
                    mac_tail = cells[3].replace(":", "")[-6:].lower()
                    break
        return f"{kind}-{mac_tail}"
    except (OSError, ValueError, IndexError):
        return "nonet"


def get_connection_id(settings) -> str:
    lan = read_bool(settings, "use_lancache", True) if settings is not None else False
    return f"{'lan' if lan else 'wan'}-{_network_fingerprint()}"


def _purge_legacy_keys(settings) -> None:
    if settings is None:
        return
    for key in _LEGACY_BEST_KEYS:
        try:
            settings.remove(key)
        except (AttributeError, OSError):
            pass


def _best_keys(base_best_key: str, settings):
    _purge_legacy_keys(settings)
    connection = get_connection_id(settings)
    return f"{base_best_key}_{connection}", f"{base_best_key}_mbps_{connection}"


def get_start_concurrency(
    settings,
    cap_key: str = "max_downloads",
    auto_key: str = "auto_max_downloads",
    best_key: str = "auto_tune_best",
    default_start: int = DEFAULT_START,
    default_cap: int = 8,
) -> int:
    cap = get_cap(settings, cap_key, default_cap)
    if not is_auto_enabled(settings, auto_key):
        return cap
    best_key, _ = _best_keys(best_key, settings)
    start = max(1, min(cap, read_int(settings, best_key, default_start)))
    return max(MIN_CONCURRENCY, start) if cap >= MIN_CONCURRENCY else start


def decide_next(prev_n: int, prev_mbps: float, best_n: int, best_mbps: float,
                cap: int, errors: int = 0):
    cap = clamp(cap, 1, MAX_CONCURRENCY)
    prev_n = clamp(prev_n, 1, cap)
    best_n = clamp(best_n, 1, cap)
    floor = MIN_CONCURRENCY if cap >= MIN_CONCURRENCY else 1
    if errors > 0:
        return max(floor, prev_n // 2), best_n, best_mbps
    if prev_mbps <= SPEED_EPS_BPS:
        return prev_n, best_n, best_mbps
    if best_mbps <= 0:
        return prev_n, prev_n, prev_mbps
    if prev_mbps > best_mbps * IMPROVE_FACTOR:
        return min(cap, prev_n + STEP), prev_n, prev_mbps
    if prev_mbps < best_mbps * DEGRADE_FACTOR:
        return max(floor, prev_n - STEP), best_n, best_mbps
    return prev_n, best_n, best_mbps


def record_result(settings, best_key: str, prev_n: int, prev_mbps: float,
                  cap: int, errors: int = 0) -> int:
    if settings is None:
        return decide_next(prev_n, prev_mbps, prev_n, 0.0, cap, errors)[0]
    best_n_key, best_m_key = _best_keys(best_key, settings)
    best_n = clamp(read_int(settings, best_n_key, prev_n), 1, MAX_CONCURRENCY)
    best_mbps = read_float(settings, best_m_key, 0.0)
    next_n, new_best_n, new_best_mbps = decide_next(
        prev_n, prev_mbps, best_n, best_mbps, cap, errors
    )
    try:
        settings.setValue(best_n_key, int(new_best_n))
        settings.setValue(best_m_key, float(new_best_mbps))
        settings.sync()
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        logger.debug("Failed to persist auto-tune result: %s", exc)
    return next_n


def get_cmd_concurrency(cmd, default: int = 8) -> int:
    try:
        return int(cmd[cmd.index("-max-downloads") + 1])
    except (ValueError, IndexError, TypeError):
        return default


def set_cmd_concurrency(cmd, value: int) -> bool:
    try:
        cmd[cmd.index("-max-downloads") + 1] = str(int(value))
        return True
    except (ValueError, IndexError, TypeError):
        return False


def format_speed(bps: float) -> str:
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 ** 2:
        return f"{bps / 1024:.2f} KB/s"
    return f"{bps / (1024 ** 2):.2f} MB/s"
