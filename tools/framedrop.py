#!/usr/bin/env python3
"""Headset installer window launched from FrameDrop's Steam library entry."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TITLE = "Frame HEV"
WINDOW_SIZE = ["--width=720", "--height=420"]


def dialog(*args):
    return subprocess.run(["zenity", f"--title={TITLE}", *WINDOW_SIZE, *args],
                          text=True, capture_output=True)


def choose_action(installed):
    if installed:
        result = dialog("--list", "--radiolist", "--column=Select", "--column=Action", "--print-column=2",
                        "--text=HEV battery announcements are installed. Choose an action.",
                        "TRUE", "Install / Update", "FALSE", "Uninstall")
        if result.returncode == 0:
            action = {"Install / Update": "install", "Uninstall": "uninstall"}.get(result.stdout.strip())
            if action is None:
                raise RuntimeError("The installer window returned an unknown selection.")
            return action
    else:
        result = dialog("--question", "--ok-label=Install", "--cancel-label=Cancel",
                        "--text=Install HEV battery announcements?\n\n"
                        "Double-tap power to hear your battery level. Single press and hold keep their normal functions.\n\n"
                        "Setup downloads the original voice clips and enables announcements at startup. Keep the Frame online.")
        if result.returncode == 0:
            return "install"
    if result.returncode == 1:
        return None
    raise RuntimeError("Could not open the installer window: " + (result.stderr or "Zenity failed.").strip())


def run_action(action, *, source=ROOT, home=None, show_ui=True):
    home = Path.home() if home is None else Path(home)
    source = Path(source)
    log = home / ".local/state/frame-hev/install.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    # Steam's normal user session owns the service even when this is launched
    # from a desktop/remote-desktop session. Uninstall must work when the native
    # button service is inactive too, so it does not use install's preflight.
    runtime = Path(f"/run/user/{os.getuid()}")
    environment = dict(os.environ)
    if (runtime / "bus").is_socket():
        environment.update(XDG_RUNTIME_DIR=str(runtime), DBUS_SESSION_BUS_ADDRESS=f"unix:path={runtime}/bus")
    if action == "install":
        command = [sys.executable, str(source / "tools/install.py")]
        progress_text = "Setting up HEV announcements.\nDownloading and checking audio may take a minute."
        success_text = ("HEV battery announcements are ready.\n\n"
                        "Double-tap power while awake to hear your battery level.\n"
                        "Setup is complete; you can close this app.\n\n"
                        "Open Frame HEV again to update or uninstall.")
    elif action == "uninstall":
        command = ["bash", str(source / "tools/uninstall.sh")]
        progress_text = "Removing HEV announcements.\nPlease wait."
        success_text = ("HEV battery announcements have been removed.\n\n"
                        "Your settings and audio cache were kept. You can remove the Frame HEV library entry separately.")
    else:
        raise ValueError(f"Unknown action: {action}")
    progress = None
    try:
        with log.open("w", encoding="utf-8") as output:
            log.chmod(0o600)
            output.write(f"{TITLE} {action} — {datetime.now(timezone.utc).isoformat()}\n")
            output.flush()
            if show_ui:
                progress = subprocess.Popen(
                    # Steam displays these windows on a VR panel. A short,
                    # auto-sized progress window gets enlarged relative to the
                    # other dialogs, so keep their dimensions consistent.
                    ["zenity", f"--title={TITLE}", *WINDOW_SIZE, "--progress", "--pulsate",
                     "--no-cancel", f"--text={progress_text}"], stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = subprocess.run(command, cwd=source, env=environment, stdout=output, stderr=subprocess.STDOUT)
    finally:
        if progress:
            if progress.poll() is None:
                progress.terminate()
            progress.wait()
            progress.stdin.close()
    if show_ui:
        if result.returncode == 0:
            dialog("--info", f"--text={success_text}")
        else:
            dialog("--error", f"--text=HEV setup could not finish.\n\nThe next window shows the details.\nLog: {log}")
            dialog("--text-info", "--height=420", f"--filename={log}")
    else:
        print(log.read_text(), end="")
    print(f"Installer log: {log}")
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-ui", action="store_true", help="run without dialogs for command-line testing")
    parser.add_argument("--action", choices=("install", "uninstall"), default="install", help="action for --no-ui")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.no_ui:
            action = args.action
        else:
            if not shutil.which("zenity"):
                raise RuntimeError("Zenity is unavailable. Run bash install.sh --local from the payload folder instead.")
            action = choose_action((Path.home() / ".local/bin/frame-hev").is_file())
        return run_action(action, show_ui=not args.no_ui) if action else 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Frame HEV setup failed: {error}", file=sys.stderr)
        if not args.no_ui and shutil.which("zenity"):
            dialog("--error", f"--text=Frame HEV setup failed:\n{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
