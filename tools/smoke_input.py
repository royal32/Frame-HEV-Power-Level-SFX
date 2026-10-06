#!/usr/bin/env python3
"""Exercise the real Linux evdev path with an isolated virtual button.

No physical button events or Steam commands are issued. The daemon runs with
--dry-run. Requires the same input/uinput permissions as the Frame Steam user.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import tempfile
import time

EVENT = struct.Struct("llHHi")
KEY_POWER = 116
ROOT = Path(__file__).resolve().parents[1]


class PrivateSleepBus:
    """Emit logind-shaped signals on a private bus, never the real system bus."""
    def __init__(self):
        self.daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.bus = ctypes.c_void_p()
        self.library = ctypes.CDLL("libsystemd.so.0")
        signatures = {
            "sd_bus_new": [ctypes.POINTER(ctypes.c_void_p)],
            "sd_bus_set_address": [ctypes.c_void_p, ctypes.c_char_p],
            "sd_bus_set_bus_client": [ctypes.c_void_p, ctypes.c_int],
            "sd_bus_start": [ctypes.c_void_p],
            "sd_bus_request_name": [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint64],
            "sd_bus_emit_signal": [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
                                   ctypes.c_char_p, ctypes.c_char_p],
            "sd_bus_flush": [ctypes.c_void_p],
        }
        for name, arguments in signatures.items():
            function = getattr(self.library, name)
            function.argtypes, function.restype = arguments, ctypes.c_int
        self.library.sd_bus_close_unref.argtypes = [ctypes.c_void_p]
        self.library.sd_bus_close_unref.restype = ctypes.c_void_p
        try:
            self.address = self.daemon.stdout.readline().strip()
            assert self.address, self.daemon.stderr.read()
            self.call("sd_bus_new", ctypes.byref(self.bus))
            self.call("sd_bus_set_address", self.bus, self.address.encode())
            self.call("sd_bus_set_bus_client", self.bus, 1)
            self.call("sd_bus_start", self.bus)
            self.call("sd_bus_request_name", self.bus, b"org.freedesktop.login1", 0)
        except BaseException:
            self.close()
            raise

    def call(self, name, *arguments):
        result = getattr(self.library, name)(*arguments)
        if result < 0:
            raise RuntimeError(f"private test bus: {name}: {os.strerror(-result)}")

    def emit(self, sleeping):
        self.call("sd_bus_emit_signal", self.bus, b"/org/freedesktop/login1",
                  b"org.freedesktop.login1.Manager", b"PrepareForSleep", b"b", ctypes.c_int(sleeping))
        self.call("sd_bus_flush", self.bus)

    def close(self):
        if self.bus.value:
            self.library.sd_bus_close_unref(self.bus)
            self.bus = ctypes.c_void_p()
        if self.daemon.poll() is None:
            self.daemon.terminate()
        self.daemon.communicate(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true",
                        help="Test aux resume through real sd-bus delivery on a private bus; no actual suspend")
    args = parser.parse_args()
    key = 353 if args.resume else KEY_POWER
    name = f"Frame HEV Test {os.getpid()}"
    virtual = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    child = None
    observer = None
    sleep_bus = None
    lines = []
    buffer = b""

    def emit(value: int) -> None:
        os.write(virtual, EVENT.pack(0, 0, 1, key, value))
        os.write(virtual, EVENT.pack(0, 0, 0, 0, 0))

    def tap() -> None:
        emit(1)
        time.sleep(0.07)
        emit(0)

    def read_until(expected: str, timeout: float = 5) -> None:
        nonlocal buffer
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise AssertionError(f"Daemon exited: {child.returncode}; {lines}")
            ready, _, _ = select.select([child.stdout], [], [], 0.1)
            if not ready:
                continue
            buffer += os.read(child.stdout.fileno(), 65536)
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                decoded = line.decode(errors="replace")
                lines.append(decoded)
                print(decoded, flush=True)
                if expected in decoded:
                    return
        raise AssertionError(f"Timed out waiting for {expected!r}; {lines}")

    try:
        fcntl.ioctl(virtual, 0x40045564, 1)  # UI_SET_EVBIT(EV_KEY)
        fcntl.ioctl(virtual, 0x40045565, key)
        setup = struct.pack("HHHH80sI", 6, 1, 1, 1, name.encode(), 0)
        fcntl.ioctl(virtual, 0x405C5503, setup)  # UI_DEV_SETUP
        fcntl.ioctl(virtual, 0x5501)  # UI_DEV_CREATE
        device = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and device is None:
            for candidate in Path("/sys/class/input").glob("event*"):
                try:
                    if (candidate / "device/name").read_text().strip() == name:
                        device = Path("/dev/input") / candidate.name
                        break
                except OSError:
                    continue
            time.sleep(0.05)
        assert device, "Virtual input device did not appear"
        observer = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        environment = {k: v for k, v in os.environ.items() if k != "NOTIFY_SOCKET"}
        if args.resume:
            sleep_bus = PrivateSleepBus()
            environment["DBUS_SYSTEM_BUS_ADDRESS"] = sleep_bus.address
        with tempfile.TemporaryDirectory(prefix="frame-hev-smoke-") as temporary:
            child = subprocess.Popen(
                [sys.executable, "-u", str(ROOT / "frame_hev.py"), "run",
                 "--device-name", name, "--button", "aux" if args.resume else "power", "--dry-run", "--lock-file",
                 str(Path(temporary) / "daemon.lock")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment,
            )
            read_until("Ready")
            if args.resume:
                tap()  # Leave an undecided gesture, then reset it on sleep.
                sleep_bus.emit(True)
                read_until("logind: preparing for sleep")
                time.sleep(0.5)
                sleep_bus.emit(False)  # No event on the watched aux device.
                read_until("logind: resumed")
                tap()
                time.sleep(0.10)
                tap()  # Resume guard discards these taps.
                time.sleep(1.1)
                tap()
                time.sleep(0.10)
                tap()  # A fresh gesture after the guard must announce.
                read_until("action=announce")
                child.terminate()
                tail = child.communicate(timeout=5)[0].decode(errors="replace")
                lines.extend(tail.splitlines())
                assert child.returncode == 0, lines
                assert sum("action=announce" in line for line in lines) == 1, lines
                assert not any("action=short" in line or "action=long" in line for line in lines), lines
                print("PASS: private-bus aux resume, wake guard, fresh gesture, and idle shutdown")
                return
            # A quick double tap must announce once and suppress native sleep.
            tap()
            time.sleep(0.10)
            tap()
            read_until("action=announce")
            time.sleep(0.65)
            assert not select.select([observer], [], [], 0)[0], "Grab leaked button events"
            assert not any("action=short" in line for line in lines), lines
            # A normal single tap still reaches the native short-press action.
            tap()
            read_until("action=short")
            # Holding power produces one long action; release must not sleep.
            emit(1)
            read_until("action=long")
            emit(0)
            time.sleep(0.5)
            child.terminate()
            tail = child.communicate(timeout=5)[0].decode(errors="replace")
            print(tail, end="")
            lines.extend(tail.splitlines())
            assert sum("action=announce" in x for x in lines) == 1, lines
            assert sum("action=short" in x for x in lines) == 1, lines
            assert sum("action=long" in x for x in lines) == 1, lines
            # Closing the daemon's fd restores events to an existing observer.
            tap()
            assert select.select([observer], [], [], 1)[0], "Grab was not released on stop"
            data = os.read(observer, EVENT.size * 32)
            events = [EVENT.unpack_from(data, i) for i in range(0, len(data), EVENT.size)]
            assert any(e[2:] == (1, KEY_POWER, 1) for e in events), events
            print("PASS: double/single/hold, exclusive delivery, and fail-open release")
    finally:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        if observer is not None:
            os.close(observer)
        if sleep_bus:
            sleep_bus.close()
        try:
            fcntl.ioctl(virtual, 0x5502)  # UI_DEV_DESTROY
        finally:
            os.close(virtual)


if __name__ == "__main__":
    main()
