# AccelaCLI

AccelaCLI is a command-line and service-oriented application for downloading
and preparing Steam depot-based game installations from manifest ZIP files or
Steam AppIDs. It is the stripped, headless variant of the original project:
it does not require a graphical interface and is designed to be usable from
scripts, automation, CI jobs, and long-running service processes.

The entire project is AI-generated. The source code, refactoring, CLI design,
service mode, documentation, and dependency cleanup were produced with the
assistance of AI agents. Use it carefully, review the code for your own
deployment requirements, and do not treat it as independently audited software.

When using `--appid`, AccelaCLI retrieves the manifest bundle from Hubcap.
That operation requires a Hubcap manifest API key. Configure it through the
`ACCELACLI_HUBCAP_API_KEY` environment variable, or pass
`--hubcap-api-key` for a one-shot command or service startup. Existing
`--manifest FILE.zip` downloads do not need a Hubcap API key.

## Credits

AccelaCLI is based on and credits the original **ASSella** project, whose
download logic and application foundations this CLI version adapts.

## What it does

At a high level, AccelaCLI can:

- Parse, inspect, validate, and list depots from manifest ZIP files.
- Resolve Steam AppIDs and selected DLC AppIDs through the existing Steam
  metadata APIs.
- Download selected depots using the bundled DepotDownloader runtime.
- Control download concurrency for automated workloads.
- Generate installation metadata and Steam ACF files where applicable.
- Optionally apply Goldberg, Steamless, EOS Proxy, and Netsock-related
  post-processing.
- Download and verify optional components before they are used.
- Configure alternate component sources using URLs, local files, or local
  folders.
- Run as an authenticated HTTP service with job submission, status reporting,
  retained events, and cancellation.
- Handle Ctrl+C cancellation and terminate active downloader processes cleanly.

AccelaCLI is not intended to recreate the original GUI. GUI themes, library
browsing, interactive terminal menus, SLSsteam/GreenLuma configuration
management, Fake AppID management, and other GUI-only workflows are outside
the scope of this tree.

The core CLI and service are cross-platform across Windows, Linux, and macOS,
provided Python 3.13+, .NET 9, and the required Python dependencies are
installed. Optional integrations remain platform-specific: Steamless
post-processing is Windows-only, Netsock is Linux/SteamOS-only, and some
Steam-specific post-processing is naturally limited by the host platform.

## Quick start

### Windows Command Prompt

```cmd
AccelaCLI.cmd manifest inspect --manifest "C:\manifests\game.zip"
AccelaCLI.cmd manifest validate --manifest "C:\manifests\game.zip"
AccelaCLI.cmd download --manifest "C:\manifests\game.zip" --destination "C:\Games"
```

### PowerShell

```powershell
.\AccelaCLI.ps1 manifest inspect --manifest "C:\manifests\game.zip"
.\AccelaCLI.ps1 download --appid 123456 --destination "C:\Games"
```

### Linux or macOS

```bash
./AccelaCLI manifest inspect --manifest "/path/to/game.zip"
./AccelaCLI download --appid 123456 --destination "/games"
```

The launchers require Python 3.13 or newer. The equivalent direct invocation
is:

```text
python3 src/main.py manifest inspect --manifest /path/to/game.zip
```

## Manifest commands

```text
AccelaCLI manifest inspect --manifest FILE.zip
AccelaCLI manifest validate --manifest FILE.zip
AccelaCLI manifest list-depots --manifest FILE.zip
```

Add `--json` to produce machine-readable output suitable for scripts.

For AppID downloads:

```powershell
$env:ACCELACLI_HUBCAP_API_KEY = "YOUR_KEY"
.\AccelaCLI.ps1 download --appid 123456 --destination "C:\Games"
```

Alternatively:

```text
AccelaCLI download --appid 123456 --destination PATH --hubcap-api-key KEY
```

## Download commands

```text
AccelaCLI download --appid APPID --destination PATH
AccelaCLI download --manifest FILE.zip --destination PATH
AccelaCLI download --manifest FILE.zip --destination PATH --depots DEPOT_ID...
AccelaCLI download --manifest FILE.zip --destination PATH --dlcs DLC_APPID...
```

Useful options include:

- `--max-downloads N` to control concurrent depot downloads.
- `--goldberg` / `--no-goldberg`.
- `--steamless` / `--no-steamless`.
- `--eos-proxy` / `--no-eos-proxy`.
- `--netsock` / `--no-netsock`.
- `--config PATH` to use component source overrides.
- `--json` for structured events and results.

## Optional components

```text
AccelaCLI components status
AccelaCLI components install goldberg
AccelaCLI components install steamless
AccelaCLI components defaults --goldberg --steamless
AccelaCLI components config
```

Component archives are verified before installation. The generated TOML
configuration can override component sources with HTTPS URLs, local files, or
local directories.

## Service mode

AccelaCLI can expose the same download path through an authenticated local HTTP
service:

```text
AccelaCLI serve --host 127.0.0.1 --port 8765
```

AppID jobs submitted to the service also require the Hubcap key to be
configured when the service starts:

```text
AccelaCLI serve --host 127.0.0.1 --port 8765 --hubcap-api-key KEY
```

The service provides:

- `GET /health`
- `POST /jobs`
- `GET /jobs/JOB_ID`
- `POST /jobs/JOB_ID/cancel`

The service generates an authentication token when one is not supplied. Keep
the service bound to a trusted interface and protect the token.

## Documentation

More detailed command behavior, component configuration, service requests, and
Ctrl+C handling are documented in [docs/cmd.md](docs/cmd.md). The current
feature status and deliberately deferred work are tracked in
[docs/cmd-roadmap.md](docs/cmd-roadmap.md).

## License and deployment

Review the source, bundled dependencies, and applicable upstream licenses before
redistributing or deploying AccelaCLI. This repository contains integrations
with third-party projects and runtime components whose terms may differ from
the terms of this source tree.
