"""Non-interactive command-line entry point for AccelaCLI."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
import zipfile
from pathlib import Path
from typing import Any


class CommandOutput:
    """Write human-readable or machine-readable command events."""

    def __init__(self, json_mode: bool, event_sink=None) -> None:
        self.json_mode = json_mode
        self.event_sink = event_sink

    def event(self, name: str, **data: Any) -> None:
        payload = {"event": name, **data}
        if self.event_sink is not None:
            self.event_sink(payload)
            return
        if self.json_mode:
            print(json.dumps(payload, sort_keys=True), flush=True)
        elif data:
            details = " ".join(f"{key}={value}" for key, value in data.items())
            print(f"[{name}] {details}", flush=True)
        else:
            print(f"[{name}]", flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="AccelaCLI",
        description="Download Steam depots from an AccelaCLI manifest.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    manifest_commands = subparsers.add_parser(
        "manifest", help="Inspect and validate manifest ZIP files"
    ).add_subparsers(dest="manifest_command", required=True)
    for name, help_text in (
        ("inspect", "Show manifest metadata, depots, and DLC entries"),
        ("validate", "Validate ZIP structure and manifest contents"),
        ("list-depots", "List downloadable depot IDs"),
    ):
        command = manifest_commands.add_parser(name, help=help_text)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--json", action="store_true", help="Emit JSON output")

    components = subparsers.add_parser(
        "components", help="Manage optional components and defaults"
    ).add_subparsers(dest="components_command", required=True)
    status = components.add_parser("status", help="Show installed component status")
    status.add_argument("--json", action="store_true", help="Emit JSON output")
    status.add_argument("--config", type=Path, help="Component source TOML path")
    install = components.add_parser("install", help="Download and verify a component")
    install.add_argument(
        "component",
        choices=("goldberg", "steamless"),
    )
    install.add_argument("--json", action="store_true", help="Emit JSON output")
    install.add_argument("--config", type=Path, help="Component source TOML path")
    config = components.add_parser(
        "config", help="Create and explain the component source configuration"
    )
    config.add_argument("--path", type=Path, help="Create/show a config at this path")
    config.add_argument("--json", action="store_true", help="Emit JSON output")
    defaults = components.add_parser(
        "defaults", help="Set default component behavior for downloads"
    )
    for option, setting in (
        ("goldberg", "auto_apply_goldberg"),
        ("steamless", "use_steamless"),
        ("eos-proxy", "enable_eosproxy_default"),
        ("netsock", "enable_netsock_default"),
    ):
        defaults.add_argument(
            f"--{option}",
            dest=setting,
            action=argparse.BooleanOptionalAction,
            default=None,
        )
    defaults.add_argument("--json", action="store_true", help="Emit JSON output")

    download = subparsers.add_parser("download", help="Download depots from a manifest")
    source = download.add_mutually_exclusive_group(required=True)
    source.add_argument("--appid", type=int, help="AppID to fetch from Hubcap")
    source.add_argument(
        "--manifest",
        type=Path,
        help="Path to an existing manifest ZIP",
    )
    download.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="Steam library or download directory",
    )
    download.add_argument(
        "--depots",
        nargs="+",
        metavar="DEPOT_ID",
        help="Depot IDs to download; defaults to all compatible depots",
    )
    download.add_argument(
        "--dlcs",
        nargs="+",
        metavar="DLC_APPID",
        help="DLC AppIDs to resolve and include in the download",
    )
    download.add_argument(
        "--max-downloads",
        type=int,
        default=8,
        metavar="N",
        help="Maximum concurrent download chunks (default: 8)",
    )
    for option, setting, label in (
        ("goldberg", "auto_apply_goldberg", "Apply Goldberg after download"),
        ("steamless", "use_steamless", "Run Steamless after download"),
        ("eos-proxy", "enable_eosproxy_default", "Apply EOS Proxy after download"),
        ("netsock", "enable_netsock_default", "Enable Netsock launch option"),
    ):
        download.add_argument(
            f"--{option}",
            dest=setting,
            action=argparse.BooleanOptionalAction,
            default=None,
            help=f"{label} (default: existing setting)",
        )
    download.add_argument("--branch", help="Manifest branch when using --appid")
    download.add_argument(
        "--platform",
        choices=("windows", "linux", "macos"),
        default="windows" if sys.platform == "win32" else sys.platform,
        help="Platform used for automatic depot selection (default: current platform)",
    )
    download.add_argument(
        "--json",
        action="store_true",
        help="Emit newline-delimited JSON events instead of human-readable output",
    )
    download.add_argument(
        "--config",
        type=Path,
        help="Component source TOML path (default: generated AccelaCLI config)",
    )
    serve = subparsers.add_parser(
        "serve", help="Run the authenticated programmatic HTTP service"
    )
    serve.add_argument("--host", default="127.0.0.1", help="Bind address (default: loopback)")
    serve.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    serve.add_argument(
        "--token",
        help="Authentication token; generated and printed if omitted",
    )
    serve.add_argument(
        "--config",
        type=Path,
        help="Component source TOML path (default: generated AccelaCLI config)",
    )
    return parser


def _existing_manifest(path_arg: Path) -> Path:
    path = path_arg.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Manifest does not exist: {path}")
    if path.suffix.lower() != ".zip":
        raise ValueError(f"Manifest must be a ZIP file: {path}")
    return path


def _read_manifest(path: Path) -> dict[str, Any]:
    from core.tasks.process_zip_task import ProcessZipTask

    manifest = _existing_manifest(path)
    try:
        with zipfile.ZipFile(manifest) as archive:
            bad_file = archive.testzip()
            if bad_file:
                raise ValueError(f"Corrupt ZIP member: {bad_file}")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid manifest ZIP: {manifest}") from exc

    game_data = ProcessZipTask().run(str(manifest))
    if not game_data:
        raise ValueError("Manifest did not produce any metadata")
    return game_data


def _manifest_summary(game_data: dict[str, Any], manifest: Path) -> dict[str, Any]:
    depots = []
    for depot_id, data in (game_data.get("depots") or {}).items():
        data = data if isinstance(data, dict) else {}
        depots.append(
            {
                "id": str(depot_id),
                "description": data.get("desc"),
                "oslist": data.get("oslist"),
                "size": data.get("size")
                or (game_data.get("manifest_sizes") or {}).get(depot_id),
                "manifest": (game_data.get("manifests") or {}).get(depot_id)
                or (game_data.get("manifests") or {}).get(str(depot_id)),
                "has_key": bool(data.get("key")),
            }
        )
    return {
        "manifest": str(manifest),
        "appid": game_data.get("appid"),
        "game_name": game_data.get("game_name"),
        "branch": game_data.get("branch", "public"),
        "depots": depots,
        "dlcs": [
            {"id": str(dlc_id), "description": description}
            for dlc_id, description in (game_data.get("dlcs") or {}).items()
        ],
        "disabled_depots": [
            {"id": str(depot_id), "description": description}
            for depot_id, description in (game_data.get("disabled_depots") or {}).items()
        ],
    }


def run_manifest_command(args: argparse.Namespace) -> int:
    output = CommandOutput(args.json)
    manifest = _existing_manifest(args.manifest)
    if args.manifest_command == "validate":
        summary = _manifest_summary(_read_manifest(manifest), manifest)
        output.event(
            "manifest_valid",
            manifest=str(manifest),
            appid=summary["appid"],
            depots=len(summary["depots"]),
            dlcs=len(summary["dlcs"]),
        )
        return 0

    summary = _manifest_summary(_read_manifest(manifest), manifest)
    if args.manifest_command == "inspect":
        if args.json:
            print(json.dumps(summary, sort_keys=True), flush=True)
        else:
            print(f"Manifest: {summary['manifest']}")
            print(f"AppID: {summary.get('appid') or 'unknown'}")
            print(f"Game: {summary.get('game_name') or 'unknown'}")
            print(f"Branch: {summary.get('branch') or 'public'}")
            print("Depots:")
            for depot in summary["depots"]:
                print(
                    f"  {depot['id']} | {depot.get('description') or 'unknown'} | "
                    f"key={'yes' if depot['has_key'] else 'no'}"
                )
            print("DLCs:")
            for dlc in summary["dlcs"]:
                print(f"  {dlc['id']} | {dlc.get('description') or 'unknown'}")
        return 0

    if args.manifest_command == "list-depots":
        if args.json:
            print(json.dumps({"depots": summary["depots"]}, sort_keys=True), flush=True)
        else:
            for depot in summary["depots"]:
                print(depot["id"])
        return 0
    raise ValueError(f"Unsupported manifest command: {args.manifest_command}")


def _select_depots(
    game_data: dict[str, Any],
    requested: list[str] | None,
    platform: str,
) -> list[str]:
    available = {str(depot_id): data for depot_id, data in game_data["depots"].items()}
    if requested:
        missing = [depot_id for depot_id in requested if depot_id not in available]
        if missing:
            raise ValueError(f"Unknown depot(s): {', '.join(missing)}")
        return requested

    selected: list[str] = []
    for depot_id, depot_data in available.items():
        oslist = str(depot_data.get("oslist") or "").lower()
        description = str(depot_data.get("desc") or "").lower()
        if "macos" in oslist or "[macos]" in description or "macosx" in oslist:
            continue
        if not oslist or platform in oslist or f"[{platform}]" in description:
            selected.append(depot_id)
    return selected


def _resolve_dlc_depots(
    game_data: dict[str, Any], dlc_ids: list[str] | None
) -> list[str]:
    """Resolve DLC AppIDs to depot IDs using the existing Steam metadata API."""
    if not dlc_ids:
        return []

    from core.steam_api import get_app_depots

    depots = game_data.setdefault("depots", {})
    resolved: list[str] = []
    unresolved: list[str] = []
    for dlc_id in dlc_ids:
        dlc_id = str(dlc_id)
        if dlc_id in depots:
            resolved.append(dlc_id)
            continue
        metadata = get_app_depots(dlc_id) or {}
        found = False
        for depot_id, depot_data in metadata.items():
            if not str(depot_id).isdigit() or not isinstance(depot_data, dict):
                continue
            depot_id = str(depot_id)
            existing = depots.get(depot_id, {})
            if not isinstance(existing, dict):
                existing = {}
            merged = {**depot_data, **existing}
            merged.setdefault("desc", f"[DLC {dlc_id}] Depot {depot_id}")
            merged["dlcappid"] = dlc_id
            depots[depot_id] = merged
            resolved.append(depot_id)
            found = True
        if not found:
            unresolved.append(dlc_id)

    if unresolved:
        raise ValueError(
            "Could not resolve DLC AppID(s) to downloadable depots: "
            + ", ".join(unresolved)
        )
    return list(dict.fromkeys(resolved))


def _set_netsock(appid: str, enabled: bool) -> None:
    if sys.platform != "linux":
        raise RuntimeError("Netsock is only supported on Linux/SteamOS")

    from utils.yaml_config_manager import (
        add_launch_option,
        get_netsock_so_launch_path,
        get_user_config_path,
        remove_launch_option,
        ensure_netsock_binary,
    )

    config_path = get_user_config_path()
    if not config_path.exists():
        raise RuntimeError(
            f"SLSsteam config was not found: {config_path}; cannot configure Netsock"
        )
    if enabled:
        if not ensure_netsock_binary():
            raise RuntimeError("Could not install or locate netsock.so")
        command = f'"env LD_AUDIT=\\"{get_netsock_so_launch_path()}\\" %command%"'
        if not add_launch_option(config_path, str(appid), command):
            raise RuntimeError("Failed to enable Netsock launch option")
    elif not remove_launch_option(config_path, str(appid)):
        raise RuntimeError("Failed to disable Netsock launch option")


def run_components_command(args: argparse.Namespace) -> int:
    from utils.settings import get_settings
    from utils.component_sources import ensure_config_file

    output = CommandOutput(args.json)
    settings = get_settings()
    if args.components_command == "config":
        path = ensure_config_file(args.path)
        if args.json:
            print(json.dumps({"config": str(path)}, sort_keys=True), flush=True)
        else:
            print(f"Component source config: {path}")
            print(
                "Edit the TOML file to map component files to URLs or local paths. "
                "Run 'AccelaCLI components config' again to locate it."
            )
        return 0

    if args.components_command == "status":
        from utils.component_manager import list_status

        ensure_config_file(args.config)
        statuses = list_status()
        statuses.pop("slscheevo", None)
        statuses["eos-proxy"] = {
            "key": "eos-proxy",
            "managed": True,
            "default_enabled": settings.value(
                "enable_eosproxy_default", False, type=bool
            ),
        }
        statuses["netsock"] = {
            "key": "netsock",
            "managed": True,
            "supported": sys.platform == "linux",
            "default_enabled": settings.value(
                "enable_netsock_default", False, type=bool
            ),
        }
        if args.json:
            print(json.dumps(statuses, sort_keys=True), flush=True)
        else:
            for key, status in statuses.items():
                print(
                    f"{key}: installed={status.get('installed')} "
                    f"current={status.get('current')}"
                )
        return 0

    if args.components_command == "install":
        from utils.component_manager import download_component

        ok, message = download_component(args.component, config_path=args.config)
        if not ok:
            raise RuntimeError(message)
        output.event("component_installed", component=args.component, message=message)
        return 0

    values = {
        "auto_apply_goldberg": args.auto_apply_goldberg,
        "use_steamless": args.use_steamless,
        "enable_eosproxy_default": args.enable_eosproxy_default,
        "enable_netsock_default": args.enable_netsock_default,
    }
    changed = {}
    for key, value in values.items():
        if value is not None:
            settings.setValue(key, value)
            changed[key] = value
            if key == "use_steamless":
                settings.setValue("use_steamless_aio", value)
    settings.sync()
    if args.json:
        print(json.dumps({"changed": changed}, sort_keys=True), flush=True)
    else:
        for key, value in changed.items():
            print(f"{key}={str(value).lower()}")
    return 0


def _manifest_path(args: argparse.Namespace, output: CommandOutput) -> Path:
    from core.morrenus_api import download_manifest, get_selected_branch

    if args.manifest:
        path = args.manifest.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Manifest does not exist: {path}")
        return path

    branch = args.branch or get_selected_branch(args.appid)
    output.event("manifest_fetch_started", appid=args.appid, branch=branch)
    path, error = download_manifest(str(args.appid), branch=branch)
    if error or not path:
        raise RuntimeError(error or "Manifest download returned no file")
    return Path(path)


def run_download(
    args: argparse.Namespace,
    logger: logging.Logger,
    event_sink=None,
    task_sink=None,
) -> int:
    from core.tasks.download_depots_task import DownloadDepotsTask
    from core.tasks.process_zip_task import ProcessZipTask
    from managers.cli_manager import CLITaskManager
    from utils.component_sources import ensure_config_file

    ensure_config_file(args.config)
    from utils.settings import get_settings

    output = CommandOutput(args.json, event_sink=event_sink)
    manifest = _manifest_path(args, output)
    destination = args.destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    output.event("manifest_processing_started", manifest=str(manifest))
    game_data = ProcessZipTask().run(str(manifest))
    if not game_data or not game_data.get("depots"):
        raise RuntimeError("Manifest contains no downloadable depots")

    dlc_depots = _resolve_dlc_depots(game_data, args.dlcs)
    if dlc_depots:
        output.event(
            "dlc_depots_resolved",
            dlcs=[str(dlc_id) for dlc_id in args.dlcs],
            depots=dlc_depots,
        )
    requested_depots = list(args.depots or []) + dlc_depots
    selected = _select_depots(
        game_data,
        requested_depots or None,
        args.platform,
    )
    if not selected:
        raise RuntimeError(f"No compatible depots found for platform {args.platform}")

    game_data["selected_depots_list"] = selected
    output.event(
        "download_started",
        appid=game_data.get("appid"),
        game_name=game_data.get("game_name") or "Unknown",
        depots=selected,
        destination=str(destination),
    )

    task = DownloadDepotsTask()
    if task_sink is not None:
        task_sink(task)
    errors: list[str] = []
    task.progress.connect(lambda message: output.event("progress", message=message))
    task.progress_percentage.connect(
        lambda percent: output.event("progress_percentage", percent=percent)
    )
    task.error.connect(lambda error: errors.append(str(error[1])))
    try:
        task.run(
            game_data,
            selected,
            str(destination),
            max_downloads=args.max_downloads,
        )
    except KeyboardInterrupt:
        task.stop()
        output.event("download_cancelled")
        return 130

    if errors:
        raise RuntimeError(errors[-1])
    if task.process is not None:
        raise RuntimeError("Download task did not terminate cleanly")

    settings = get_settings()
    for setting in (
        "auto_apply_goldberg",
        "enable_eosproxy_default",
        "enable_netsock_default",
    ):
        value = getattr(args, setting, None)
        if value is not None:
            settings.setValue(setting, value)
    steamless = getattr(args, "use_steamless", None)
    if steamless is not None:
        settings.setValue("use_steamless", steamless)
        settings.setValue("use_steamless_aio", steamless)
    settings.sync()
    CLITaskManager(settings, logger).run_post_processing(
        game_data,
        task,
        str(destination),
        component_config_path=args.config,
    )
    netsock_arg = getattr(args, "enable_netsock_default", None)
    if netsock_arg is not None:
        _set_netsock(str(game_data.get("appid")), netsock_arg)
    elif settings.value("enable_netsock_default", False, type=bool):
        _set_netsock(str(game_data.get("appid")), True)
    output.event(
        "download_completed",
        appid=game_data.get("appid"),
        game_name=game_data.get("game_name") or "Unknown",
        destination=str(destination),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        from utils.logger import setup_logging

        if getattr(args, "json", False):
            with contextlib.redirect_stdout(sys.stderr):
                logger = setup_logging()
        else:
            logger = setup_logging()
        if getattr(args, "json", False):
            for handler in logging.getLogger().handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.setLevel(logging.CRITICAL + 1)
        from utils.component_sources import ensure_config_file

        if args.command == "components" and args.components_command == "config":
            ensure_config_file(args.path)
        else:
            ensure_config_file(getattr(args, "config", None))

        if args.command == "manifest":
            return run_manifest_command(args)
        if args.command == "components":
            return run_components_command(args)
        if args.command == "download":
            return run_download(args, logger)
        if args.command == "serve":
            from service import run_service

            return run_service(args, logger)
    except KeyboardInterrupt:
        if "logger" in locals():
            logger.info("Command cancelled by user.")
        if getattr(args, "json", False):
            print(json.dumps({"event": "cancelled"}, sort_keys=True), flush=True)
        else:
            print("cancelled", file=sys.stderr, flush=True)
        return 130
    except (FileNotFoundError, ModuleNotFoundError, RuntimeError, ValueError, OSError) as exc:
        if "logger" in locals():
            logger.error("%s", exc)
        if getattr(args, "json", False):
            print(
                json.dumps({"event": "error", "error": str(exc)}, sort_keys=True),
                flush=True,
            )
        else:
            print(f"error: {exc}", file=sys.stderr, flush=True)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
