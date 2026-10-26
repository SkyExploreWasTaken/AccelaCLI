"""Headless post-processing for completed depot downloads."""

import os
import shutil
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import Any, Dict, Optional

from core.tasks.download_depots_task import DownloadDepotsTask

from utils.paths import Paths
from utils.steam_manifest import get_game_directory, write_acf_file
class CLITaskManager:
    """CLI version of TaskManager that handles all post-processing steps without UI."""

    def __init__(self, settings, logger):
        self.settings = settings
        self.logger = logger
        self.game_data: Optional[Dict[str, Any]] = None
        self.download_task: Optional[DownloadDepotsTask] = None
        self.current_dest_path: Optional[str] = None
        self.component_config_path = None

    def run_post_processing(
        self,
        game_data: Optional[Dict[str, Any]],
        download_task: DownloadDepotsTask,
        dest_path: str,
        component_config_path=None,
    ):
        """Run all post-processing steps after download completion."""
        if not game_data:
            return

        self.game_data = game_data
        self.download_task = download_task
        self.current_dest_path = dest_path
        self.component_config_path = component_config_path
        # Get size on disk
        size_on_disk = 0
        if self.download_task:
            size_on_disk = self.download_task.total_download_size_for_this_job
            self.logger.info(f"Retrieved SizeOnDisk from download task: {size_on_disk}")

        # Move manifests to depotcache FIRST — depotcache must be populated before
        # the SLS install|appid|index command fires so Steam can verify local files.
        self._move_manifests_to_depotcache()

        # Write app token to file for non-integrated installs
        self._write_app_token(dest_path)

        # Create ACF / trigger SLS install (after depotcache is ready)
        self._create_acf_file(size_on_disk)

        # Save main depot info
        self._save_main_depot_info()


        # Set Linux binary permissions
        if sys.platform == "linux":
            self._set_linux_binary_permissions()


        # Auto-apply Goldberg after download completion
        auto_apply_goldberg = self.settings.value(
            "auto_apply_goldberg", False, type=bool
        )
        if auto_apply_goldberg and self.game_data and self.current_dest_path:
            # Goldberg is an optional component, so it may not be installed.
            # The CMD path has no interactive fallback.
            from utils.component_manager import is_component_available
            from utils.component_manager import download_component

            if not is_component_available("goldberg"):
                self.logger.info("Goldberg is not installed; downloading it before application...")
                installed, message = download_component(
                    "goldberg", config_path=self.component_config_path
                )
                if not installed:
                    self.logger.error(f"Goldberg download failed: {message}")
                    return
                self.logger.info(f"Goldberg component ready: {message}")

            if not is_component_available("goldberg"):
                self.logger.error("Goldberg is still unavailable after installation.")
            else:
                game_directory = get_game_directory(self.current_dest_path, self.game_data)
                self.logger.info("Auto-applying Goldberg after download completion")
                self._apply_goldberg(
                    game_directory,
                    str(self.game_data.get("appid", "")),
                    self.game_data.get("game_name", ""),
                )
                if sys.platform == "linux":
                    self._set_linux_permissions(game_directory)

        # Steamless processing
        steamless_enabled = self.settings.value(
            "use_steamless", False, type=bool
        ) or self.settings.value("use_steamless_aio", False, type=bool)
        appid = self.game_data.get("appid") if self.game_data else None
        is_dlc_only = False
        if appid:
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc_only = is_dlc_only_mode(str(appid))

        if steamless_enabled and not is_dlc_only and not self._is_current_job_linux():
            self._run_steamless()

        # EOS Proxy auto-apply
        if self.settings.value("enable_eosproxy_default", False, type=bool) and self.current_dest_path:
            try:
                game_directory = get_game_directory(self.current_dest_path, self.game_data)
                if game_directory and os.path.isdir(game_directory):
                    from utils.eos_detector import EOSDetector
                    status = EOSDetector.get_proxy_status(game_directory)
                    if status in ("inactive", "stale"):
                        if EOSDetector.apply_proxy(game_directory):
                            self.logger.info(f"Automatically applied EOSProxy to {game_directory}")
            except Exception as e:
                self.logger.debug(f"Error applying EOSProxy in CLI: {e}")

        self.logger.info("All post-processing steps completed")

    def _create_acf_file(self, size_on_disk: int):
        """Create Steam ACF manifest file"""
        if not self.game_data or not self.current_dest_path:
            return

        appid = self.game_data.get("appid")

        # 1. Always write/update our local metadata.json fallback
        try:
            from utils.AccelaCLI_metadata import write_AccelaCLI_metadata
            write_AccelaCLI_metadata(self.current_dest_path, self.game_data, size_on_disk)
        except Exception as e:
            self.logger.error(f"Failed to write metadata JSON file: {e}")

        # 2. If ACF-Independent mode is active, delegate manifest creation entirely to Steam natively
        try:
            from utils.slssteam_integration import install_via_sls, _experimental_mode_enabled
            if _experimental_mode_enabled():
                self.logger.info("ACF-Independent Mode is active. Delegating manifest creation to Steam natively.")
                if appid and appid not in ("0", "N/A", "unknown"):
                    install_via_sls(
                        appid=str(appid),
                        game_name=self.game_data.get("game_name", ""),
                        library_path=self.current_dest_path or "",
                    )
                return
        except Exception as e:
            self.logger.error(f"Error in SLSsteam install flow for {appid}: {e}")

        # 3. Fallback: Write standard Steam .acf manifest file when experimental mode is disabled
        if appid:
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc_only = is_dlc_only_mode(str(appid))
            if is_dlc_only:
                self.logger.info("DLC Only mode active. Skipping base game .acf manifest generation.")
                return

        self.logger.info("Generating Steam .acf manifest file...")

        try:
            acf_path = write_acf_file(
                self.current_dest_path,
                self.game_data,
                size_on_disk,
                include_depots=True,
                log_proton=True,
            )
            if acf_path:
                self.logger.info(f"Created .acf file at {acf_path}")
        except OSError as e:
            self.logger.error(f"Error creating .acf file: {e}")

    def _write_app_token(self, dest_path: str):
        """Write app token to file for non-integrated installs."""
        if not self.game_data:
            return

        app_token = self.game_data.get("app_token")
        if not app_token:
            return

        game_dir = get_game_directory(dest_path, self.game_data)
        token_file = os.path.join(game_dir, "apptoken.txt")

        try:
            os.makedirs(game_dir, exist_ok=True)
            with open(token_file, "w") as f:
                f.write(app_token)
            self.logger.info(f"Wrote app token to {token_file}")
        except OSError as e:
            self.logger.error(f"Failed to write app token to file: {e}")

    def _move_manifests_to_depotcache(self):
        """Move manifests from temp to depotcache"""
        if not self.game_data or not self.current_dest_path:
            return

        import tempfile

        temp_manifest_dir = os.path.join(tempfile.gettempdir(), "mistwalker_manifests")
        if not os.path.exists(temp_manifest_dir):
            return

        target_depotcache_dir = os.path.join(self.current_dest_path, "depotcache")

        try:
            os.makedirs(target_depotcache_dir, exist_ok=True)
            manifests_map = self.game_data.get("manifests", {})

            if not manifests_map:
                shutil.rmtree(temp_manifest_dir)
                return

            moved_count = 0
            for depot_id, manifest_gid in manifests_map.items():
                manifest_filename = f"{depot_id}_{manifest_gid}.manifest"
                source_path = os.path.join(temp_manifest_dir, manifest_filename)
                dest = os.path.join(target_depotcache_dir, manifest_filename)
                if os.path.exists(source_path):
                    shutil.move(source_path, dest)
                    moved_count += 1

            self.logger.info(f"Moved {moved_count} manifest files to depotcache.")
            shutil.rmtree(temp_manifest_dir)
        except (OSError, shutil.Error) as e:
            self.logger.error(f"Failed to move manifests to depotcache: {e}")

    def _save_main_depot_info(self):
        """Save main depot ID and manifest to persistent file"""
        from pathlib import Path
        from utils.cmd_helpers import get_base_path

        if not self.game_data:
            return

        appid = self.game_data.get("appid")
        if not appid:
            return

        selected_depots = self.game_data.get("selected_depots_list", [])
        all_manifests = self.game_data.get("manifests", {})

        if not selected_depots or not all_manifests:
            return

        try:
            depots_dir = Path(get_base_path()) / "depots"
            depots_dir.mkdir(parents=True, exist_ok=True)
            depot_file = depots_dir / f"{appid}.depot"

            existing_entries = {}
            if depot_file.exists():
                try:
                    for line in depot_file.read_text().splitlines():
                        parts = [p.strip() for p in line.split(":")]
                        if len(parts) >= 2:
                            existing_entries[parts[0]] = line
                except Exception:
                    pass

            for depot_id_raw in selected_depots:
                depot_id = str(depot_id_raw)
                manifest_id = all_manifests.get(depot_id)
                if manifest_id:
                    existing_entries[depot_id] = f"{depot_id}: {manifest_id}"

            with open(depot_file, "w") as f:
                for entry_line in existing_entries.values():
                    f.write(entry_line + "\n")

            self.logger.info(f"Saved depot info for app {appid}: {len(existing_entries)} depot(s)")
        except OSError as e:
            self.logger.error(f"Failed to save depot info: {e}")

    def _set_linux_binary_permissions(self):
        """Set executable permissions for Linux binaries"""
        if not self.game_data or not self.current_dest_path:
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)

        if not os.path.exists(game_directory):
            return

        self._set_linux_permissions(game_directory)

    def _apply_goldberg(self, game_directory: str, appid: str, game_name: str) -> bool:
        """Apply Goldberg files to a game directory (CLI mode)."""
        self.logger.info(
            f"Applying Goldberg for game: {game_name} (AppID: {appid}) in {game_directory}"
        )

        if not game_directory or not os.path.exists(game_directory):
            self.logger.warning(f"Game directory not found: {game_directory}")
            return False

        is_windows = sys.platform == "win32"
        is_linux = sys.platform == "linux"

        found_dirs = set()

        if is_windows:
            for root, _, files in os.walk(game_directory):
                for fname in files:
                    if fname.lower() in ("steam_api.dll", "steam_api64.dll"):
                        found_dirs.add(root)
        elif is_linux:
            for root, _, files in os.walk(game_directory):
                for fname in files:
                    fname_lower = fname.lower()
                    if fname_lower in ("libsteam_api.so", "steam_api.dll", "steam_api64.dll"):
                        found_dirs.add(root)

        if not found_dirs:
            if is_windows:
                self.logger.info("No steam_api DLLs found in game directory tree")
            else:
                self.logger.info("No libsteam_api.so or steam_api DLL found in game directory tree")
            return False

        # Source Goldberg directory (optional component, downloaded on demand)
        goldberg_src = Paths.deps("Goldberg")
        if not goldberg_src.exists():
            self.logger.error(
                f"Goldberg is not installed: {goldberg_src}. "
                "Download it from Settings -> Tools -> Optional Components."
            )
            return False

        def detect_elf_architecture(file_path: str):
            try:
                with open(file_path, "rb") as f:
                    magic = f.read(4)
                    if magic != b"\x7fELF":
                        return None
                    elf_class = f.read(1)
                    if elf_class == b"\x01":
                        return "32"
                    elif elf_class == b"\x02":
                        return "64"
            except (IOError, OSError):
                pass
            return None

        processed = 0
        try:
            for dest_dir in found_dirs:
                has_dll = any(
                    os.path.exists(os.path.join(dest_dir, base))
                    for base in ("steam_api.dll", "steam_api64.dll")
                )
                has_native_so = os.path.exists(
                    os.path.join(dest_dir, "libsteam_api.so")
                )
                if is_windows:
                    original_dlls_in_dir = set()
                    for base in ("steam_api.dll", "steam_api64.dll"):
                        if os.path.exists(os.path.join(dest_dir, base)):
                            original_dlls_in_dir.add(base)

                    for base in ("steam_api.dll", "steam_api64.dll"):
                        src_path = os.path.join(dest_dir, base)
                        if os.path.exists(src_path):
                            try:
                                target_path = src_path + ".valve"
                                if not os.path.exists(target_path):
                                    os.replace(src_path, target_path)
                                    self.logger.info(f"Renamed {src_path} -> {target_path}")
                            except OSError as e:
                                self.logger.warning(f"Failed to rename {src_path}: {e}")

                    for base in original_dlls_in_dir:
                        src_dll = goldberg_src / "windows" / base
                        dest_dll = os.path.join(dest_dir, base)
                        try:
                            if src_dll.exists():
                                shutil.copy2(str(src_dll), dest_dll)
                                self.logger.info(f"Copied Goldberg DLL {src_dll} -> {dest_dll}")
                            else:
                                self.logger.warning(f"Goldberg DLL not found in deps: {src_dll}")
                        except (OSError, shutil.Error) as e:
                            self.logger.warning(f"Failed to copy Goldberg DLL: {e}")
                
                elif is_linux:
                    # Check if this directory has native .so files or DLLs
                    if has_native_so:
                        # Native Linux: .so file logic with architecture detection
                        original_so_path = os.path.join(dest_dir, "libsteam_api.so")
                        if os.path.exists(original_so_path):
                            detected_arch = detect_elf_architecture(original_so_path)
                            self.logger.info(f"Detected {detected_arch}-bit ELF in {dest_dir}")

                            try:
                                target_path = original_so_path + ".valve"
                                if not os.path.exists(target_path):
                                    os.replace(original_so_path, target_path)
                                    self.logger.info(f"Renamed {original_so_path} -> {target_path}")
                            except OSError as e:
                                self.logger.warning(f"Failed to rename {original_so_path}: {e}")

                            if detected_arch == "64":
                                src_so = goldberg_src / "linux" / "libsteam_api64.so"
                            else:
                                src_so = goldberg_src / "linux" / "libsteam_api.so"
                            dest_so = os.path.join(dest_dir, "libsteam_api.so")

                            try:
                                if src_so.exists():
                                    shutil.copy2(str(src_so), dest_so)
                                    self.logger.info(f"Copied {src_so.name} to {dest_so}")
                                else:
                                    self.logger.warning(f"Source .so not found: {src_so}")
                            except OSError as e:
                                self.logger.warning(f"Failed to copy Goldberg SO: {e}")

                    if has_dll:
                        # DLL logic (Proton/Wine)
                        original_dlls_in_dir = set()
                        for base in ("steam_api.dll", "steam_api64.dll"):
                            dll_path = os.path.join(dest_dir, base)
                            if os.path.exists(dll_path):
                                original_dlls_in_dir.add(base)

                        for base in ("steam_api.dll", "steam_api64.dll"):
                            src_dll_path = os.path.join(dest_dir, base)
                            if os.path.exists(src_dll_path):
                                try:
                                    target_path = src_dll_path + ".valve"
                                    if not os.path.exists(target_path):
                                        os.replace(src_dll_path, target_path)
                                        self.logger.info(f"Renamed {src_dll_path} -> {target_path}")
                                except OSError as e:
                                    self.logger.warning(f"Failed to rename {src_dll_path}: {e}")

                        for base in original_dlls_in_dir:
                            src_dll = goldberg_src / "windows" / base
                            dest_dll = os.path.join(dest_dir, base)
                            try:
                                if src_dll.exists():
                                    shutil.copy2(str(src_dll), dest_dll)
                                    self.logger.info(f"Copied Goldberg DLL {src_dll} -> {dest_dll}")
                                else:
                                    self.logger.warning(f"Goldberg DLL not found in deps: {src_dll}")
                            except (OSError, shutil.Error) as e:
                                self.logger.warning(f"Failed to copy Goldberg DLL: {e}")

                goldberg_platform_src = goldberg_src / ("windows" if is_windows else "linux")
                
                for item in goldberg_src.iterdir():
                    if item.name.lower() in (
                        "windows", "linux", "genints",
                        "steam_api.dll", "steam_api64.dll",
                        "libsteam_api.so", "libsteam_api64.so",
                        "steam_appid.txt",
                    ):
                        continue
                    dest_path = os.path.join(dest_dir, item.name)
                    try:
                        if item.is_dir():
                            shutil.copytree(str(item), dest_path, dirs_exist_ok=True)
                            self.logger.info(f"Copied dir {item} -> {dest_path}")
                        else:
                            shutil.copy2(str(item), dest_path)
                            self.logger.info(f"Copied file {item} -> {dest_path}")
                    except (OSError, shutil.Error) as e:
                        self.logger.warning(f"Failed to copy {item} to {dest_path}: {e}")

                if goldberg_platform_src.exists():
                    for item in goldberg_platform_src.iterdir():
                        item_name_lower = item.name.lower()
                        if item_name_lower == "steam_appid.txt":
                            continue
                        # On Linux, only copy .so files if the game has native .so files
                        # On Windows, only copy .dll files if the game has .dll files
                        if is_linux and item_name_lower.endswith(".so"):
                            if not has_native_so:
                                continue
                        elif is_windows and item_name_lower.endswith(".dll"):
                            if not has_dll:
                                continue
                        dest_path = os.path.join(dest_dir, item.name)
                        try:
                            if item.is_dir():
                                shutil.copytree(str(item), dest_path, dirs_exist_ok=True)
                            else:
                                shutil.copy2(str(item), dest_path)
                        except (OSError, shutil.Error) as e:
                            self.logger.warning(f"Failed to copy {item}: {e}")

                try:
                    appid_file = os.path.join(dest_dir, "steam_appid.txt")
                    with open(appid_file, "w", encoding="utf-8") as f:
                        f.write(str(appid))
                    self.logger.info(f"Wrote steam_appid.txt to {appid_file}")
                except OSError as e:
                    self.logger.warning(f"Failed to write steam_appid.txt: {e}")

                processed += 1

            self.logger.info(f"Applied Goldberg files to {processed} folder(s).")
            
            self.logger.info("Generating interface headers for Goldberg...")
            try:
                from managers.task_manager import TaskManager
                
                for dest_dir in found_dirs:
                    valve_files = []
                    for fname in os.listdir(dest_dir):
                        fname_lower = fname.lower()
                        if fname_lower.endswith(".valve") and "steam_api" in fname_lower:
                            is_64bit = "64" in fname_lower
                            valve_files.append((os.path.join(dest_dir, fname), is_64bit))
                    
                    if valve_files:
                        steam_settings_dir = os.path.join(dest_dir, "steam_settings")
                        
                        for valve_path, is_64bit in valve_files:
                            if is_windows and not valve_path.lower().endswith(".dll.valve"):
                                continue
                            
                            fname_lower = valve_path.lower()
                            if not (fname_lower.endswith(".dll.valve") or fname_lower.endswith(".so.valve")):
                                continue
                            
                            success = TaskManager._run_generate_interfaces_for_file(
                                dest_dir,
                                valve_path,
                                is_64bit,
                                goldberg_src,
                            )
                            
                            if success:
                                interfaces_file = os.path.join(dest_dir, "steam_interfaces.txt")
                                if os.path.exists(interfaces_file):
                                    try:
                                        os.makedirs(steam_settings_dir, exist_ok=True)
                                        dest_interfaces = os.path.join(steam_settings_dir, "steam_interfaces.txt")
                                        shutil.move(interfaces_file, dest_interfaces)
                                        self.logger.info(f"Moved steam_interfaces.txt to {steam_settings_dir}")
                                    except OSError as e:
                                        self.logger.warning(f"Failed to move steam_interfaces.txt: {e}")
            except Exception as e:
                self.logger.warning(f"Failed to generate interfaces: {e}")
            
            return True

        except (OSError, shutil.Error) as e:
            self.logger.exception(f"Error applying Goldberg: {e}")
            return False

    def _is_current_job_linux(self) -> bool:
        """Check if current job is downloading Linux native depots."""
        if not self.game_data:
            return False
        selected_depots = self.game_data.get("selected_depots_list") or []
        depots = self.game_data.get("depots") or {}
        found_any = False
        for d_id in selected_depots:
            d_data = depots.get(str(d_id)) or {}
            if not d_data:
                continue
            found_any = True
            oslist = (d_data.get("oslist") or "").lower()
            desc = (d_data.get("desc") or "").lower()
            is_linux = (oslist == "linux") or ("[linux]" in desc) or ("linux" in desc)
            if not is_linux:
                return False
        return found_any

    def _set_linux_permissions(self, game_directory: str):
        """Set executable permissions for Linux binaries."""
        self.logger.info(f"Setting executable permissions in: {game_directory}")
        try:
            from managers.emulation.chmod import ChmodMixin
            chmod_count = ChmodMixin._run_chmod_recursive(game_directory)
            if chmod_count > 0:
                self.logger.info(
                    f"Set executable permissions for {chmod_count} Linux binaries"
                )
        except Exception as e:
            self.logger.warning(f"Failed to set Linux binary permissions: {e}")

    def _run_steamless(self):
        """Unpack SteamStub-protected Windows executables in place."""
        if sys.platform != "win32" or not self.current_dest_path or not self.game_data:
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)
        if not os.path.isdir(game_directory):
            self.logger.warning("Steamless skipped; game directory not found: %s", game_directory)
            return

        try:
            from deps.steamless import Options, unpack
        except ImportError as exc:
            self.logger.error("Steamless implementation is unavailable: %s", exc)
            return

        executables = []
        for root, _, files in os.walk(game_directory):
            for filename in files:
                if filename.lower().endswith(".exe"):
                    executables.append(os.path.join(root, filename))

        if not executables:
            self.logger.info("Steamless skipped; no Windows executables found.")
            return

        self.logger.info("Running Steamless on %d executable(s)", len(executables))
        options = Options()
        unpacked = 0
        detected = 0
        for executable in sorted(executables):
            try:
                tool_output = StringIO()
                with redirect_stdout(tool_output), redirect_stderr(tool_output):
                    result = unpack(executable, options, in_place=True)
            except (OSError, RuntimeError, ValueError) as exc:
                self.logger.warning("Steamless failed for %s: %s", executable, exc)
                continue

            output = tool_output.getvalue().strip()
            if output:
                self.logger.debug("Steamless output for %s:\n%s", executable, output)

            if result == 0:
                unpacked += 1
                self.logger.info("Steamless unpacked %s", executable)
            elif result == 2:
                detected += 1
                self.logger.warning("Steamless detected DRM but could not unpack %s", executable)

        self.logger.info(
            "Steamless completed: %d unpacked, %d detected-but-failed, %d unchanged",
            unpacked,
            detected,
            len(executables) - unpacked - detected,
        )
