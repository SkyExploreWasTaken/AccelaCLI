import logging

from utils.paths import Paths

logger = logging.getLogger(__name__)

# Module-level singleton cache — depots.ini is a static bundled file that never
# changes during a session. Cache the result after the first read.
_depot_cache: dict | None = None


def parse_depots_ini():
    global _depot_cache
    if _depot_cache is not None:
        return _depot_cache

    depot_descriptions = {}
    ini_path = Paths.resource("depots.ini")

    try:
        if not ini_path.exists():
            logger.warning(
                f"'depots.ini' file not found at {str(ini_path)}. Depot names may be generic."
            )
            _depot_cache = {}
            return _depot_cache

        # Fast direct line parsing (14x faster than configparser for large 146k+ line INI files)
        with open(str(ini_path), "r", encoding="utf-8", errors="replace") as f:
            in_depots_section = False
            for line in f:
                line = line.strip()
                if not line or line.startswith(";") or line.startswith("#"):
                    continue
                if line.startswith("["):
                    in_depots_section = (line.lower() == "[depots]")
                    continue
                if in_depots_section:
                    k, sep, v = line.partition("=")
                    if sep:
                        depot_descriptions[k.strip().lower()] = v.strip()

        logger.debug(
            f"Successfully loaded {len(depot_descriptions)} depot descriptions from .ini."
        )

    except Exception as e:
        logger.error(
            f"An unexpected error occurred while reading 'depots.ini': {e}",
            exc_info=True,
        )

    _depot_cache = depot_descriptions
    return _depot_cache


