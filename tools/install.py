#!/usr/bin/env python3
"""Install or update Frame HEV in the Steam user's home directory."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UNIT = "frame-hev.service"


def command(args, *, check=True, capture=False):
    result = subprocess.run(args, text=True, capture_output=capture)
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise RuntimeError(f"{' '.join(map(str, args))} failed ({result.returncode})" +
                           (f"\n{detail}" if detail else ""))
    return result


def systemctl(*args, check=True):
    return command(["systemctl", "--user", *args], check=check, capture=True)


def preflight():
    if sys.platform != "linux":
        raise RuntimeError("Run install.sh from the project root to install over SSH from this computer.")
    if os.getuid() == 0:
        raise RuntimeError("Run as the normal Steam user without sudo.")
    for name in ("systemctl", "pw-play", "busctl"):
        if not shutil.which(name):
            raise RuntimeError(f"{name} is missing. This installer requires a Steam Frame running SteamOS.")
    runtime = Path(f"/run/user/{os.getuid()}")
    if (runtime / "bus").exists():
        os.environ.setdefault("XDG_RUNTIME_DIR", str(runtime))
        os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    if systemctl("is-active", "--quiet", "steamos-powerbuttond.service", check=False).returncode:
        raise RuntimeError("Wake the Frame and leave Steam running, then retry. "
                           "Its native steamos-powerbuttond service must be active.")


def snapshot(path):
    if path.is_symlink():
        raise RuntimeError(f"Refusing to replace a symlink: {path}")
    return (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None


def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def restore(path, previous):
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write(path, *previous)


def install(source=ROOT, home=None, *, offline=False, source_dir=None):
    """Stage and validate before touching a running installation; roll back failures."""
    home = Path.home() if home is None else Path(home)
    source = Path(source)
    preflight()
    required = ["frame_hev.py", "assets/manifest.json", "assets/README.md", "README.md",
                "VERSION", "LICENSE", "tools/fetch_sounds.py", "tools/control.sh",
                "tools/uninstall.sh", "config/environment", "systemd/frame-hev.service"]
    for relative in required:
        if not (source / relative).is_file():
            raise RuntimeError(f"Incomplete download: {relative} is missing. Extract the whole release ZIP.")

    app = home / ".local/share/frame-hev"
    unit = home / ".config/systemd/user" / UNIT
    launcher = home / ".local/bin/frame-hev"
    settings = home / ".config/frame-hev/environment"
    if app.is_symlink():
        raise RuntimeError(f"Refusing to replace a symlink: {app}")
    old_unit, old_launcher = snapshot(unit), snapshot(launcher)
    old_active = systemctl("is-active", "--quiet", UNIT, check=False).returncode == 0
    old_enabled = systemctl("is-enabled", "--quiet", UNIT, check=False).returncode == 0
    app.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".frame-hev-install-", dir=app.parent))
    staged = work / "application"
    backup = work / "previous"
    preserve_work = False
    moved_new = made_settings = False
    try:
        staged.mkdir()
        for relative in ("frame_hev.py", "VERSION", "LICENSE", "README.md",
                         "assets/manifest.json", "assets/README.md"):
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
        shutil.copyfile(source / "tools/uninstall.sh", staged / "uninstall.sh")
        fetch = [sys.executable, str(source / "tools/fetch_sounds.py"),
                 "--quiet", "--output", str(staged / "assets"), "--cache-dir", str(app / "assets"),
                 "--cache-dir", str(source / "assets")]
        if offline:
            fetch.append("--offline")
        if source_dir:
            fetch.extend(["--source-dir", str(source_dir)])
        print("Preparing original HEV audio (verified local copies are reused)...", flush=True)
        command(fetch)
        print("Checking headset compatibility...", flush=True)
        command([sys.executable, str(staged / "frame_hev.py"), "doctor"], capture=True)

        try:
            # Leave the current service intact until staging and doctor succeed.
            systemctl("stop", UNIT, check=old_active)
            if app.exists():
                app.replace(backup)
            staged.replace(app)
            moved_new = True
            atomic_write(unit, (source / "systemd/frame-hev.service").read_bytes(), 0o644)
            atomic_write(launcher, (source / "tools/control.sh").read_bytes(), 0o755)
            if not settings.exists():
                atomic_write(settings, (source / "config/environment").read_bytes(), 0o600)
                made_settings = True
            systemctl("daemon-reload")
            systemctl("reset-failed", UNIT, check=False)
            systemctl("enable", "--now", UNIT)
            systemctl("is-active", "--quiet", UNIT)
        except Exception:
            print("Installation failed; restoring the previous installation...", file=sys.stderr)
            try:
                systemctl("stop", UNIT, check=False)
                if moved_new:
                    shutil.rmtree(app)
                if backup.exists():
                    backup.replace(app)
                restore(unit, old_unit)
                restore(launcher, old_launcher)
                if made_settings:
                    settings.unlink(missing_ok=True)
                systemctl("daemon-reload")
                systemctl("enable" if old_enabled else "disable", UNIT, check=old_enabled)
                if old_active:
                    systemctl("start", UNIT)
            except Exception as rollback_error:
                preserve_work = True
                print(f"Rollback needs attention: {rollback_error}\nBackup retained at {work}", file=sys.stderr)
            raise
    finally:
        if not preserve_work:
            shutil.rmtree(work)
    version = (app / "VERSION").read_text().strip()
    print(f"\nFrame HEV {version} is installed and enabled at startup.")
    print("Double-tap power while awake to hear the battery level.")
    print("Single press still sleeps; hold still opens the power menu.")
    print("Try now:   ~/.local/bin/frame-hev announce")
    print("Settings: ~/.config/frame-hev/environment")
    print("Remove:   ~/.local/bin/frame-hev uninstall")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="only use verified local audio")
    parser.add_argument("--source-dir", type=Path, help="import loose WAV files from an installed Half-Life game")
    args = parser.parse_args()
    try:
        install(offline=args.offline, source_dir=args.source_dir)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"\nFrame HEV could not be installed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
