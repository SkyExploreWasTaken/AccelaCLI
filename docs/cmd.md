# Command-line usage

AccelaCLI is a command-line-only application. It does not create a Qt GUI or
wait for interactive depot selections.

## Download from an AppID

```text
AccelaCLI download --appid 123 --destination "C:\Games\SteamLibrary"
```

The manifest is fetched from Hubcap using the configured API key. Configure it
for headless use with an environment variable:

```powershell
$env:ACCELACLI_HUBCAP_API_KEY = "YOUR_KEY"
AccelaCLI download --appid 123 --destination "C:\Games\SteamLibrary"
```

You can also pass `--hubcap-api-key KEY` to `download` or `serve`, although
environment variables are preferable because command-line arguments may be
visible to other local processes. The command automatically selects depots
compatible with the current platform. Downloads from an existing
`--manifest FILE.zip` do not require a Hubcap API key.

## Download an existing manifest

```text
AccelaCLI download --manifest "C:\manifests\game.zip" --destination "C:\Games\SteamLibrary" --depots 123 456
```

`--depots` is optional. When omitted, all compatible depots in the manifest
are selected. `--destination` is required so automated jobs never silently
write to an unexpected directory.

### DLC IDs

Manifest ZIPs may contain both downloadable depot entries and DLC AppIDs.
They are related but not interchangeable: a DLC AppID identifies optional
content, while `--depots` currently accepts depot IDs that have decryption
keys and manifest data. The easiest reliable workflow is to inspect the ZIP
first and pass the listed depot IDs:

```cmd
AccelaCLI manifest inspect --manifest "C:\manifests\game.zip"
AccelaCLI download --manifest "C:\manifests\game.zip" --destination "C:\Games" --depots 123 456 789
```

Automatic DLC-AppID-to-depot expansion is tracked in the roadmap because the
mapping and keys are not always present in the ZIP itself. You can now ask the
command to resolve DLC AppIDs through Steam metadata:

```cmd
AccelaCLI download --manifest "C:\manifests\game.zip" --destination "C:\Games" --dlcs 123456 123457
```

You may combine `--dlcs` with `--depots`. Unresolvable DLC AppIDs fail clearly
instead of silently downloading only the base game. Resolved depot keys and
manifest IDs still come from the manifest/cache/downloader fallback chain.

## Current download settings

The downloader currently inherits two existing settings:

- **LanCache** is enabled by default. It asks DepotDownloader to use a local
  LanCache when available and falls back to a direct connection if LanCache
  fails.
- **CDN probing** is enabled by default. It probes available CDN routes to
  select a lower-latency route.

These remain roadmap items. Download concurrency is already available:

```cmd
--max-downloads 8
```

The default is 8 concurrent chunks.

## Optional components

Optional components are separately downloaded tools or replacement files,
such as Goldberg and Steamless. They are not required for every
game:

- Goldberg replaces Steam API files for some offline/multiplayer fixes.
- Steamless removes or bypasses certain DRM wrappers where supported.
- EOS Proxy and Netsock support specific multiplayer/networking setups.

The download command supports toggles for the post-processing
components already used safely by the GUI:

```cmd
--goldberg
--no-goldberg
--steamless
--no-steamless
--eos-proxy
--no-eos-proxy
```

If omitted, each option preserves its existing AccelaCLI setting. When supplied,
the value is saved as the new default and affects the existing post-processing
implementation.

Steamless post-processing is headless in the CMD build. On Windows, enabled
downloads scan the game directory for executables and unpack SteamStub-protected
files in place, retaining each original as `<file>.original`.

Component management commands are also available:

```cmd
AccelaCLI components status
AccelaCLI components install goldberg
AccelaCLI components install steamless
AccelaCLI components defaults --goldberg --steamless --eos-proxy
AccelaCLI components defaults --no-goldberg --no-steamless
```

### Component source configuration

AccelaCLI creates an editable TOML file at the normal AccelaCLI data location on
the first CLI launch. Locate or create it with:

```cmd
AccelaCLI components config
```

The generated file documents the format. Sources are configured per component,
platform, and file. A source may be an HTTPS URL, a local file, or a local
directory containing the named file:

```toml
[components.goldberg.windows]
"steam_api64.dll" = "C:\\AccelaCLI\\components\\steam_api64.dll"
"steam_api.dll" = "https://example.invalid/steam_api.dll"

[components.goldberg.linux]
"libsteam_api.so" = "https://example.invalid/libsteam_api.so"
```

For Goldberg, the platform section automatically maps these filenames into
Goldberg's `windows`, `linux`, or `macos` directory. You can also use an
explicit relative path such as `"linux/libsteam_api.so"` if preferred.

The normal verified R2 component archive remains the baseline. Configured
files replace matching files when their sources are available; unavailable
custom sources are logged and the archive copy is retained. To use a different
configuration file for a download or component installation, pass:

```cmd
AccelaCLI download --config "C:\configs\AccelaCLI-components.toml" ...
AccelaCLI components install goldberg --config "C:\configs\AccelaCLI-components.toml"
```

This allows, for example, Linux Goldberg files to come from one URL while a
Windows DLL comes from a local file, without changing the built-in defaults.

The download command also supports:

```cmd
--netsock
--no-netsock
```

Netsock is Linux/SteamOS-only and changes the per-AppID launch option using
the existing SLSsteam configuration helper. It requires an existing SLSsteam
configuration and does not manage Fake AppIDs.

## Automation output

Add `--json` to emit newline-delimited JSON events. Every event has an
`event` property. A successful command emits `download_completed`; failures
emit `error` and return exit code `1`.

For manifest inspection, `--json` emits a single structured document. Logs are
written to stderr so stdout remains safe to pipe into another program.

Exit codes:

- `0` — download and post-processing completed
- `1` — invalid input, manifest, network, or download failure
- `2` — invalid command-line syntax
- `130` — cancelled with Ctrl+C

Ctrl+C is handled by the CLI. Active DepotDownloader processes are stopped,
temporary work is cleaned up by the normal task path, and the command exits
with code `130` instead of displaying a Python traceback. In JSON mode, a
`cancelled` event is emitted.

The executable can be run directly from a source checkout:

```text
python src\main.py download --manifest "C:\manifests\game.zip" --destination "C:\Games\SteamLibrary"
```

### Launchers and Ctrl+C

On Windows, `AccelaCLI.cmd` is convenient from Command Prompt, but `cmd.exe`
may display its own `Terminer le programme de commandes (O/N)` confirmation
when Ctrl+C is pressed inside a foreground batch file. This prompt cannot be
disabled reliably by Python while retaining normal foreground process
control. Use the PowerShell launcher to avoid the batch-layer prompt:

```powershell
.\AccelaCLI.ps1 download --manifest "C:\manifests\game.zip" --destination "C:\Games"
```

The Python entry point also works directly:

```powershell
python .\src\main.py download --manifest "C:\manifests\game.zip" --destination "C:\Games"
```

On Linux or macOS, use the included `AccelaCLI` launcher:

```sh
./AccelaCLI download --manifest "/path/to/game.zip" --destination "/games"
```

It uses `python3` when available, falls back to `python`, and forwards Ctrl+C
directly to the Python process.

## HTTP service mode

The service is a separate, long-running mode. It cannot be combined with a
download or another one-shot command:

```cmd
AccelaCLI serve --host 127.0.0.1 --port 8765
```

The service generates an authentication token and prints it once at startup.
The health endpoint is public:

```text
GET /health
```

All job endpoints require the `X-AccelaCLI-Token` header. Submit a download with
the same fields as the CLI:

```text
POST /jobs
Content-Type: application/json
X-AccelaCLI-Token: TOKEN

{
  "manifest": "C:\\manifests\\game.zip",
  "destination": "C:\\Games",
  "goldberg": true,
  "max_downloads": 8
}
```

The response contains a job ID. Query its status and retained events with:

```text
GET /jobs/JOB_ID
X-AccelaCLI-Token: TOKEN
```

Cancel a queued or active job with:

```text
POST /jobs/JOB_ID/cancel
X-AccelaCLI-Token: TOKEN
```

Jobs are processed serially to avoid running multiple download tasks in the same
process. Bind to `127.0.0.1` unless network access is explicitly needed. If a
custom component-source file is required, start the service with
`--config PATH` or include `"config": "PATH"` in a job request.
