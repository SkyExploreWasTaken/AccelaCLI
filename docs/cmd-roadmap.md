# CMD roadmap

The CMD application is intentionally focused on non-interactive downloads.
Features below are candidates for the programmatic interface, not a plan to
recreate the GUI.

## Confirmed scope

- Keep positional ZIP support out of the first contract unless a compatibility
  alias is useful; `download --manifest` is explicit and script-friendly.
- Keep helper/update scans, library management, themes, and UI settings out of
  scope.
- Do not require Hubcap API-key configuration from the CMD interface.
- Keep `serve` mutually exclusive with download and other one-shot commands.
- Exclude Fake AppID management from the CMD roadmap.

## Roadmap

### Manifest and depot tools

Implemented:

- `AccelaCLI manifest inspect --manifest <zip>` — print AppID, branch, game name,
  depots, DLC metadata, manifest IDs, sizes, and key availability.
- `AccelaCLI manifest validate --manifest <zip>` — validate ZIP structure and
  parsed manifest contents.
- `AccelaCLI manifest list-depots --manifest <zip>` — list downloadable depot IDs
  for shell pipelines; use `--json` for structured output.

Remaining:

- Improve validation diagnostics for malformed or incomplete entries.
- Optional depot metadata and branch/build inspection commands.
- Resolve DLC AppIDs to their downloadable depot IDs where the manifest/API
  provides enough information, so users do not need to discover every depot
  manually. The `download --dlcs` option now performs this resolution through
  Steam metadata.

### Download controls

- `--max-downloads <n>`: control DepotDownloader's concurrent download
  connections. This is implemented now and defaults to `8`.
- Consider selective file lists only after a stable non-interactive input
  format is designed.

### Components and multiplayer

- Implemented safe component status, install, and defaults commands using the
  existing component manager.
- Implemented download toggles for Goldberg, Steamless, EOS Proxy, and Netsock.
  Existing GUI-safe post-processing is reused.
- `--goldberg` now downloads and verifies Goldberg automatically when it is not
  already installed.
- Implemented generated TOML component-source configuration with per-component,
  per-platform, per-file URL/local-path overrides and `--config` support.
- Add machine-readable preflight output for missing optional components and
  incompatible game/platform combinations.
- Steamless post-processing is implemented in the CMD path using the bundled
  headless SteamStub unpacker. Windows executables are scanned and processed
  in place with `.original` backups.

### Service mode

- Implemented `AccelaCLI serve --host <host> --port <port>` as a separate mode.
- Implemented token authentication, job submission, status, retained events,
  and cancellation.
- Jobs are serialized within the service process and use the existing download
  and post-processing implementation.
- Future work: add paginated event streaming and configurable job retention.

## Explicitly not planned

- GUI theme and appearance controls.
- GUI-only library browsing and game management.
- Interactive terminal menus.
- Automatic library-wide update helpers.
- SLSsteam or GreenLuma configuration management.
- Fake AppID management.
- LanCache and CDN probing controls until higher-priority command features are
  complete.
