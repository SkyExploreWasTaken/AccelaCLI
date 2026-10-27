"""Build the portable, single-file AccelaCLI executable."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "AccelaCLI.spec"
DIST = ROOT / "dist"
WORK = ROOT / "build-onefile"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove previous build and dist directories before building",
    )
    args = parser.parse_args()

    if args.clean:
        if WORK.exists():
            shutil.rmtree(WORK)
        executable = DIST / ("AccelaCLI.exe" if sys.platform == "win32" else "AccelaCLI")
        if executable.exists():
            executable.unlink()

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK),
        str(SPEC),
    ]
    subprocess.run(command, cwd=ROOT, check=True)

    executable = DIST / ("AccelaCLI.exe" if sys.platform == "win32" else "AccelaCLI")
    if not executable.is_file():
        raise FileNotFoundError(f"PyInstaller did not create {executable}")
    print(f"Built {DIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
