$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py (Join-Path $root "src\main.py") @args
    exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    & python (Join-Path $root "src\main.py") @args
    exit $LASTEXITCODE
}

[Console]::Error.WriteLine("AccelaCLI requires Python 3.13 or newer.")
exit 127
