#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Extract the source bundled in the native installer for this setup session."""
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    # Python accepts a ZIP appended to a native executable. The C launcher
    # passes its absolute /proc/self/exe path, even after FrameDrop renames it.
    with tempfile.TemporaryDirectory(prefix="frame-hev-setup-") as temporary:
        with zipfile.ZipFile(sys.argv[0]) as archive:
            archive.extractall(temporary)
        source = Path(temporary) / "payload"
        result = subprocess.run([sys.executable, str(source / "tools/framedrop.py"),
                                 *sys.argv[1:]], cwd=source)
        return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, zipfile.BadZipFile) as error:
        print(f"Frame HEV: could not unpack or start setup: {error}", file=sys.stderr)
        raise SystemExit(1)
