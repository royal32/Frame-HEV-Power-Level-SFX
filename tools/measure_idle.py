#!/usr/bin/env python3
"""Measure idle HEV CPU and scheduling on Linux using a virtual power button.

No button events, Steam commands, physical-device grabs, or system changes.
The child runs with dry-run actions and a separate lock. CPU time comes from
all threads' /proc schedstat runtime counters; context switches are not a
measurement of hardware wakeups or battery savings.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time


def snapshot(pid):
    threads = list(Path(f"/proc/{pid}/task").iterdir())
    runtime_ns = voluntary = involuntary = 0
    for thread in threads:
        runtime_ns += int((thread / "schedstat").read_text().split()[0])
        status = dict(line.split(":", 1) for line in (thread / "status").read_text().splitlines())
        voluntary += int(status["voluntary_ctxt_switches"])
        involuntary += int(status["nonvoluntary_ctxt_switches"])
    return runtime_ns, voluntary, involuntary, len(threads)


def measure(runtime, seconds):
    name = f"Frame HEV Idle Test {os.getpid()}"
    virtual = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    child = None
    try:
        fcntl.ioctl(virtual, 0x40045564, 1)  # UI_SET_EVBIT(EV_KEY)
        fcntl.ioctl(virtual, 0x40045565, 116)  # UI_SET_KEYBIT(KEY_POWER)
        setup = struct.pack("HHHH80sI", 6, 1, 1, 1, name.encode(), 0)
        fcntl.ioctl(virtual, 0x405C5503, setup)
        fcntl.ioctl(virtual, 0x5501)
        deadline = time.monotonic() + 5
        while True:
            found = any((path / "device/name").read_text().strip() == name
                        for path in Path("/sys/class/input").glob("event*")
                        if (path / "device/name").exists())
            if found:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Virtual device did not appear")
            time.sleep(0.05)
        with tempfile.TemporaryDirectory(prefix="frame-hev-idle-") as temporary:
            log_path = Path(temporary) / "daemon.log"
            with log_path.open("w") as log:
                child = subprocess.Popen([sys.executable, "-u", str(runtime), "run",
                                          "--device-name", name, "--dry-run", "--lock-file",
                                          str(Path(temporary) / "runtime.lock")],
                                         stdout=log, stderr=subprocess.STDOUT,
                                         env={k: v for k, v in os.environ.items() if k != "NOTIFY_SOCKET"})
                deadline = time.monotonic() + 5
                while "Ready device=" not in log_path.read_text():
                    if child.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(log_path.read_text())
                    time.sleep(0.05)
                time.sleep(1)  # Exclude startup, imports, and subscription setup.
                before = snapshot(child.pid)
                start = time.monotonic()
                time.sleep(seconds)
                elapsed = time.monotonic() - start
                after = snapshot(child.pid)
                assert child.poll() is None, log_path.read_text()
                cpu_seconds = (after[0] - before[0]) / 1_000_000_000
                result = {"runtime_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
                          "elapsed_seconds": elapsed, "cpu_seconds": cpu_seconds,
                          "cpu_percent_one_core": 100 * cpu_seconds / elapsed,
                          "voluntary_context_switches": after[1] - before[1],
                          "involuntary_context_switches": after[2] - before[2],
                          "threads": after[3]}
                child.terminate()
                child.wait(timeout=5)
                assert child.returncode == 0, log_path.read_text()
                assert "Stopped; native power handling restored" in log_path.read_text()
                return result
    finally:
        if child and child.poll() is None:
            child.kill()
            child.wait()
        fcntl.ioctl(virtual, 0x5502)
        os.close(virtual)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path(__file__).resolve().parents[1] / "frame_hev.py")
    parser.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    print(json.dumps(measure(args.runtime.resolve(), args.seconds), indent=2))


if __name__ == "__main__":
    main()
