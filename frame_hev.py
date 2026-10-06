#!/usr/bin/env python3
"""Read the Frame battery in the HEV voice; intercept power gestures safely."""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import queue
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave

LOG = logging.getLogger("frame-hev")
EV_SYN, EV_KEY, SYN_REPORT, SYN_DROPPED = 0, 1, 0, 3
KEY_POWER, KEY_AUX = 116, 353
EVENT = struct.Struct("llHHi")
DEFAULT_ASSETS = Path(__file__).resolve().parent / "assets" / "fvox"
STEAM = Path.home() / ".steam/steam/steamrtarm64/steam"
OPENVR_LIBRARY = "/opt/steamvr/bin/linuxarm64/libopenvr_api.so"
WORDS = ("zero one two three four five six seven eight nine ten eleven twelve "
         "thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split()
TENS = {20: "twenty", 30: "thirty", 40: "fourty", 50: "fifty", 60: "sixty",
        70: "seventy", 80: "eighty", 90: "ninety"}


class GestureController:
    """Two completed taps announce; single and held presses retain Steam actions.

    The intertap window begins on release. Repeats and very short bounce pulses
    are ignored. A second press reserves the first until its outcome is known.
    """
    def __init__(self, double_tap=0.350, long_hold=1.0, debounce=0.025):
        self.double_tap, self.long_hold, self.debounce = double_tap, long_hold, debounce
        self.reset()

    def reset(self):
        self.down = None
        self.pending = None
        self.second = False
        self.held_action = False
        self.last_release = None

    def tick(self, now):
        actions = []
        if self.down is not None and not self.held_action and now - self.down >= self.long_hold:
            # A held second press takes precedence: dispatching the reserved
            # short first could suspend Steam before its long-press menu opens.
            self.pending = None
            self.second = False
            self.held_action = True
            actions.append("long")
        if self.down is None and self.pending is not None and now >= self.pending + self.double_tap:
            self.pending = None
            actions.append("short")
        return actions

    def deadline(self):
        """When tick() next has work, or None while idle (so the caller can block)."""
        if self.down is not None:
            return None if self.held_action else self.down + self.long_hold
        return None if self.pending is None else self.pending + self.double_tap

    def event(self, value, now):
        if value == 2:  # Linux key repeat is never another tap.
            return self.tick(now)
        actions = self.tick(now)
        if value == 1 and self.down is None:
            if self.last_release is not None and now - self.last_release < self.debounce:
                return actions
            self.down = now
            self.second = self.pending is not None
            self.held_action = False
        elif value == 0 and self.down is not None:
            duration = now - self.down
            self.down = None
            self.last_release = now
            if self.held_action:
                self.held_action = False
            elif duration >= self.debounce:
                if self.second:
                    self.pending = None
                    actions.append("announce")
                else:
                    self.pending = now
            self.second = False
        return actions


def phrase(percent):
    if not 0 <= percent <= 100:
        raise ValueError("battery percentage must be between 0 and 100")
    if percent == 0:
        return ["armor_gone"]
    if percent == 100:
        number = ["onehundred"]
    elif percent == 25:
        number = ["twentyfive"]
    elif percent < 20:
        number = [WORDS[percent]]
    else:
        tens, units = divmod(percent, 10)
        number = [TENS[tens * 10]] + ([WORDS[units]] if units else [])
    return ["power_level_is", *number, "percent"]


def openvr_battery_percent():
    """Read SteamVR's HMD battery property in the isolated probe subprocess.

    ABI: Valve openvr_capi.h at 0924064316de3effbcd1acf1e309182a2deb1c05,
    IVRSystem_026. Its GetFloatTrackedDeviceProperty is function-table slot 23.
    Request this exact interface version; never reuse the offset with another.
    """
    library = ctypes.CDLL(OPENVR_LIBRARY)
    error_pointer = ctypes.POINTER(ctypes.c_int)
    library.VR_InitInternal.argtypes = [error_pointer, ctypes.c_int]
    library.VR_InitInternal.restype = ctypes.c_size_t
    library.VR_GetGenericInterface.argtypes = [ctypes.c_char_p, error_pointer]
    library.VR_GetGenericInterface.restype = ctypes.c_void_p
    library.VR_ShutdownInternal.argtypes = []
    library.VR_ShutdownInternal.restype = None
    error = ctypes.c_int()
    library.VR_InitInternal(ctypes.byref(error), 3)  # VRApplication_Background
    if error.value:
        raise RuntimeError(f"OpenVR background connection failed ({error.value})")
    try:
        address = library.VR_GetGenericInterface(b"FnTable:IVRSystem_026", ctypes.byref(error))
        if error.value or not address:
            raise RuntimeError(f"OpenVR IVRSystem_026 is unavailable ({error.value})")
        table = ctypes.cast(address, ctypes.POINTER(ctypes.c_void_p))
        if not table[23]:
            raise RuntimeError("OpenVR battery query is unavailable")
        get_float = ctypes.CFUNCTYPE(ctypes.c_float, ctypes.c_uint32, ctypes.c_int, error_pointer)(table[23])
        fraction = get_float(0, 1012, ctypes.byref(error))  # HMD, Prop_DeviceBatteryPercentage_Float
        if error.value or not math.isfinite(fraction) or not 0 <= fraction <= 1:
            raise RuntimeError(f"OpenVR returned an invalid HMD battery value ({error.value}, {fraction})")
        # 45% arrives as a float such as 0.449999988, so truncation is wrong.
        return int(fraction * 100 + 0.5)
    finally:
        library.VR_ShutdownInternal()


def read_battery(root=None):
    """Prefer SteamVR's displayed level; an explicit root reads sysfs only."""
    if root is None:
        try:
            # Keep native API failures and hangs away from the process holding
            # the power-device grab. No cached percentage or calibration offset.
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_openvr-battery"],
                                    capture_output=True, text=True, check=True, timeout=2)
            report = json.loads(result.stdout)
            percent = report.get("percent") if isinstance(report, dict) else None
            if type(percent) is not int or not 0 <= percent <= 100:
                raise ValueError("invalid OpenVR battery probe result")
            return "openvr:hmd", percent
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            LOG.warning("SteamVR battery unavailable (%s); using raw kernel level", error)
    return read_sysfs_battery(Path("/sys/class/power_supply") if root is None else root)


def read_sysfs_battery(root):
    candidates = []
    for path in sorted(Path(root).iterdir()):
        try:
            if (path / "type").read_text().strip() != "Battery":
                continue
            if (path / "scope").exists() and (path / "scope").read_text().strip() == "Device":
                continue
            percent = int((path / "capacity").read_text().strip())
            if not 0 <= percent <= 100:
                continue
            candidates.append((path, percent))
        except (OSError, ValueError):
            continue
    if not candidates:
        raise RuntimeError("no system battery with a valid capacity found")
    preferred = [item for item in candidates if item[0].name.startswith("max1720x_bat")]
    if len(preferred or candidates) != 1:
        raise RuntimeError("multiple system batteries found; cannot choose safely")
    return (preferred or candidates)[0]


def read_pcm(path):
    with wave.open(str(path), "rb") as source:
        if source.getcomptype() != "NONE":
            raise ValueError(f"{path}: only uncompressed PCM WAV is supported")
        fmt = source.getnchannels(), source.getsampwidth(), source.getframerate()
        frames = source.readframes(source.getnframes())
        if not frames or len(frames) != source.getnframes() * fmt[0] * fmt[1]:
            raise ValueError(f"{path}: empty or truncated WAV")
        return fmt, frames


def validate_assets(assets):
    names = sorted({name for n in range(101) for name in phrase(n)})
    common = None
    for name in names:
        fmt, _ = read_pcm(Path(assets) / (name + ".wav"))
        if common is not None and fmt != common:
            raise ValueError(f"{name}.wav: PCM format {fmt} differs from {common}")
        common = fmt
    return {"count": len(names), "channels": common[0], "sample_width": common[1], "sample_rate": common[2]}


def compose_wave(assets, percent, cache=None):
    assets = Path(assets)
    parts, common, digest = [], None, hashlib.sha256()
    for name in phrase(percent):
        path = assets / (name + ".wav")
        fmt, frames = read_pcm(path)
        if common is not None and fmt != common:
            raise ValueError(f"{path}: incompatible PCM format")
        common = fmt
        parts.append(frames)
        digest.update(name.encode() + repr(fmt).encode() + frames)
    cache = Path(cache) if cache else Path.home() / ".cache/frame-hev"
    cache.mkdir(parents=True, exist_ok=True)
    output = cache / f"{percent}-{digest.hexdigest()[:24]}.wav"
    if output.exists():
        return output
    fd, temporary = tempfile.mkstemp(prefix=".phrase-", suffix=".wav", dir=cache)
    os.close(fd)
    try:
        with wave.open(temporary, "wb") as target:
            target.setnchannels(common[0])
            target.setsampwidth(common[1])
            target.setframerate(common[2])
            target.writeframes(b"".join(parts))
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return output


def ioctl_code(direction, number, size):
    return (direction << 30) | (size << 16) | (ord("E") << 8) | number


def get_bits(fd, number, length=96):
    data = bytearray(length)
    fcntl.ioctl(fd, ioctl_code(2, number, length), data, True)
    return {n for n in range(length * 8) if data[n // 8] & (1 << (n % 8))}


def device_info(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        name = bytearray(256)
        fcntl.ioctl(fd, ioctl_code(2, 0x06, len(name)), name, True)
        return {"path": str(path), "name": name.split(b"\0")[0].decode(errors="replace"),
                "keys": sorted(get_bits(fd, 0x20 + EV_KEY))}
    finally:
        os.close(fd)


def input_inventory():
    result = []
    for path in sorted(Path("/dev/input").glob("event*")):
        try:
            result.append(device_info(path))
        except OSError as error:
            result.append({"path": str(path), "error": str(error)})
    return result


def resolve_device(button, override=None, name=None):
    name = name or ("pmic_pwrkey" if button == "power" else "gpio-keys")
    devices = [device_info(override)] if override else input_inventory()
    matches = [d for d in devices if d.get("name") == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one readable input device named {name!r}; found {len(matches)}")
    device = matches[0]
    key = KEY_POWER if button == "power" else KEY_AUX
    if key not in device["keys"]:
        raise RuntimeError(f"{name}: required key {key} missing")
    if button == "power" and device["keys"] != [KEY_POWER]:
        raise RuntimeError(f"{name}: refusing to grab a device with other keys: {device['keys']}")
    return device


def preparing_for_sleep():
    result = subprocess.run(["busctl", "--system", "get-property", "org.freedesktop.login1",
                             "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                             "PreparingForSleep"], capture_output=True, text=True, timeout=2, check=True)
    value = result.stdout.strip()
    if value not in ("b true", "b false"):
        raise RuntimeError("could not read logind PreparingForSleep")
    return value == "b true"


def suspend_offset():
    """CLOCK_BOOTTIME advances during suspend; CLOCK_MONOTONIC does not."""
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()


def stale_action(marker):
    return suspend_offset() - marker > 0.2


class ActionRunner:
    """Bounded queues keep input processing independent of subprocesses."""
    def __init__(self, args, wake=None):
        self.args = args
        self.wake = wake
        self.audio = queue.Queue(maxsize=1)
        self.steam = queue.Queue(maxsize=8)
        self.errors = queue.Queue()
        self.stop = threading.Event()
        self.processes = set()
        self.lock = threading.Lock()
        self.threads = [threading.Thread(target=self.work, args=(q,), daemon=True)
                        for q in (self.audio, self.steam)]
        for thread in self.threads:
            thread.start()

    def submit(self, action):
        if self.args.button == "aux" and action != "announce":
            return
        LOG.info("action=%s", action)
        if self.args.dry_run:
            return
        target = self.audio if action == "announce" else self.steam
        try:
            target.put_nowait((action, suspend_offset()))
        except queue.Full:
            if action == "announce":
                LOG.warning("audio queue full; announcement skipped")
            else:
                raise RuntimeError("Steam action queue full; releasing power device")

    def execute(self, command, timeout, marker):
        with self.lock:
            if self.stop.is_set():
                return
            if stale_action(marker):
                LOG.info("queued action skipped: system resumed before execution")
                return
            process = subprocess.Popen(command)
            self.processes.add(process)
        try:
            process.wait(timeout=timeout)
            if process.returncode:
                raise RuntimeError(f"command exited with status {process.returncode}: {command[0]}")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            with self.lock:
                self.processes.discard(process)

    def work(self, tasks):
        while not self.stop.is_set():
            item = tasks.get()
            if item is None:  # close() sentinel
                return
            action, marker = item
            try:
                if stale_action(marker):
                    LOG.info("action=%s skipped: queued before resume", action)
                    continue
                sleeping = preparing_for_sleep()
                # The bus query can block across a suspend/resume cycle.
                if stale_action(marker):
                    LOG.info("action=%s skipped: resumed during sleep query", action)
                    continue
                if sleeping:
                    LOG.info("action=%s skipped: preparing for sleep", action)
                    continue
                if action == "announce":
                    source, percent = read_battery()
                    LOG.info("action=announce percent=%s source=%s phrase=%s", percent, source, " ".join(phrase(percent)))
                    path = compose_wave(self.args.assets, percent)
                    self.execute(["pw-play", f"--volume={self.args.volume}", str(path)], 30, marker)
                else:
                    self.execute([str(self.args.steam), "-ifrunning", f"steam://{action}powerpress"], 5, marker)
            except Exception as error:
                LOG.error("action=%s failed: %s", action, error)
                if action != "announce":
                    self.errors.put(error)
                    if self.wake:
                        self.wake()
            finally:
                tasks.task_done()

    def close(self):
        self.stop.set()
        with self.lock:
            for process in self.processes:
                if process.poll() is None:
                    process.terminate()
        for tasks in (self.audio, self.steam):
            try:
                tasks.put_nowait(None)
            except queue.Full:
                pass  # The worker is busy and sees stop before its next get().
        for thread in self.threads:
            thread.join(timeout=2.5)


def notify_ready():
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(b"READY=1")


def run(args):
    # The lock is held until the grab is released, including during cleanup.
    cache = Path.home() / ".cache/frame-hev"
    cache.mkdir(parents=True, exist_ok=True)
    lock_path = args.lock_file or cache / "runtime.lock"
    with Path(lock_path).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("frame-hev is already running") from None
        device = resolve_device(args.button, args.device, args.device_name)
        if not args.dry_run:
            validate_assets(args.assets)
            read_battery()
            if not shutil.which("pw-play") or not Path(args.steam).is_file():
                raise RuntimeError("pw-play and the Steam executable are required")
            preparing_for_sleep()  # Verify logind before taking ownership of power.
        key = KEY_POWER if args.button == "power" else KEY_AUX
        # Signals and worker errors write here so select can block while idle.
        wake_r, wake_w = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        fd = os.open(device["path"], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        runner = None
        quit_event = threading.Event()
        previous_handlers = {}

        def wake():
            try:
                os.write(wake_w, b"\0")
            except OSError:
                pass  # Already pending, or closed during shutdown.

        def request_quit(*_):
            quit_event.set()
            wake()
        try:
            if key in get_bits(fd, 0x18):
                raise RuntimeError("button is held; release it before starting")
            # Event timestamps must use the same clock as gesture deadlines.
            fcntl.ioctl(fd, ioctl_code(1, 0xA0, 4), struct.pack("i", time.CLOCK_MONOTONIC))
            if args.button == "power":
                fcntl.ioctl(fd, ioctl_code(1, 0x90, 4), 1)
                if key in get_bits(fd, 0x18):
                    raise RuntimeError("button became held during startup; releasing grab")
            else:
                LOG.warning("aux is observe-only; its native action also fires")
            controller = GestureController(args.double_tap_ms / 1000, args.long_hold_ms / 1000,
                                           args.debounce_ms / 1000)
            runner = ActionRunner(args, wake)
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[sig] = signal.signal(sig, request_quit)
            offset = time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()
            guard_until, dropping, buffer = 0.0, False, b""
            LOG.info("Ready device=%s name=%s button=%s", device["path"], device["name"], args.button)
            notify_ready()
            while not quit_event.is_set():
                now = time.monotonic()
                new_offset = time.clock_gettime(time.CLOCK_BOOTTIME) - now
                if new_offset - offset > 0.2:
                    controller.reset()
                    guard_until = now + args.resume_guard_ms / 1000
                    buffer = b""
                    LOG.info("resume: gesture reset; discarding wake presses")
                offset = new_offset
                if not runner.errors.empty():
                    raise RuntimeError(f"power dispatch failed: {runner.errors.get()}")
                deadline = controller.deadline()
                timeout = None if deadline is None else max(0.0, deadline - now)
                readable, _, _ = select.select([fd, wake_r], [], [], timeout)
                # Suspend can occur inside select; check again before consuming
                # wake events or advancing a held-button deadline.
                now = time.monotonic()
                new_offset = time.clock_gettime(time.CLOCK_BOOTTIME) - now
                if new_offset - offset > 0.2:
                    controller.reset()
                    guard_until = now + args.resume_guard_ms / 1000
                    buffer = b""
                    LOG.info("resume: gesture reset; discarding wake presses")
                offset = new_offset
                if wake_r in readable:
                    os.read(wake_r, 64)
                if fd in readable:
                    chunk = os.read(fd, EVENT.size * 64)
                    if not chunk:
                        raise RuntimeError("input device disconnected")
                    buffer += chunk
                    while len(buffer) >= EVENT.size:
                        seconds, micros, event_type, code, value = EVENT.unpack(buffer[:EVENT.size])
                        buffer = buffer[EVENT.size:]
                        timestamp = seconds + micros / 1_000_000
                        if event_type == EV_SYN and code == SYN_DROPPED:
                            controller.reset()
                            dropping = True
                            continue
                        if dropping:
                            if event_type == EV_SYN and code == SYN_REPORT:
                                dropping = False
                            continue
                        if now < guard_until or timestamp < guard_until:
                            continue
                        if event_type == EV_KEY and code == key:
                            for action in controller.event(value, timestamp):
                                runner.submit(action)
                if now >= guard_until and not dropping:
                    for action in controller.tick(time.monotonic()):
                        runner.submit(action)
        finally:
            # Closing the file descriptor always releases EVIOCGRAB, even on error.
            os.close(fd)
            if runner:
                runner.close()
            os.close(wake_r)
            os.close(wake_w)
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
            LOG.info("Stopped; native power handling restored")


def doctor(args):
    report = {"inputs": input_inventory(), "commands": {
        "pw-play": shutil.which("pw-play"), "busctl": shutil.which("busctl"),
        "steam": str(args.steam), "steam_exists": Path(args.steam).is_file()}}
    try:
        source, percent = read_battery()
        report["battery"] = {"source": str(source), "percent": percent}
        if isinstance(source, Path):
            report["battery"]["path"] = str(source)
        try:
            raw_path, raw_percent = read_sysfs_battery(Path("/sys/class/power_supply"))
            report["battery"]["kernel"] = {"path": str(raw_path), "percent": raw_percent}
        except (OSError, RuntimeError) as error:
            report["battery"]["kernel"] = {"error": str(error)}
    except (OSError, RuntimeError) as error:
        report["battery"] = {"error": str(error)}
    try:
        report["assets"] = {"path": str(args.assets), **validate_assets(args.assets)}
    except (OSError, ValueError, wave.Error) as error:
        report["assets"] = {"path": str(args.assets), "error": str(error)}
    try:
        report["power_device"] = resolve_device("power")
    except (OSError, RuntimeError) as error:
        report["power_device"] = {"error": str(error)}
    try:
        report["preparing_for_sleep"] = preparing_for_sleep()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        report["logind"] = {"error": str(error)}
    report["ok"] = (not any("error" in report[key] for key in ("battery", "assets", "power_device"))
                    and "logind" not in report and bool(report["commands"]["pw-play"])
                    and report["commands"]["steam_exists"])
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for command in ("doctor", "announce", "run"):
        child = commands.add_parser(command)
        child.add_argument("--assets", type=Path, default=Path(os.environ.get("FRAME_HEV_ASSETS", DEFAULT_ASSETS)))
        child.add_argument("--steam", type=Path, default=Path(os.environ.get("FRAME_HEV_STEAM", STEAM)))
        child.add_argument("--volume", type=float, default=float(os.environ.get("FRAME_HEV_VOLUME", "0.65")))
        if command != "doctor":
            child.add_argument("--dry-run", action="store_true")
        if command == "announce":
            child.add_argument("--percent", type=int)
        if command == "run":
            child.add_argument("--button", choices=("power", "aux"), default=os.environ.get("FRAME_HEV_BUTTON", "power"))
            child.add_argument("--device", type=Path)
            child.add_argument("--device-name")
            child.add_argument("--lock-file", type=Path, help="override singleton lock (for isolated input tests)")
            child.add_argument("--double-tap-ms", type=float, default=float(os.environ.get("FRAME_HEV_DOUBLE_TAP_MS", "350")))
            child.add_argument("--long-hold-ms", type=float, default=float(os.environ.get("FRAME_HEV_LONG_HOLD_MS", "1000")))
            child.add_argument("--debounce-ms", type=float, default=25)
            child.add_argument("--resume-guard-ms", type=float, default=1000)
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["_openvr-battery"]:
        try:
            print(json.dumps({"percent": openvr_battery_percent()}))
            return 0
        except (OSError, RuntimeError, AttributeError) as error:
            print(str(error), file=sys.stderr)
            return 1
    arg_parser = parser()
    args = arg_parser.parse_args(argv)
    if not 0 <= args.volume <= 1:
        arg_parser.error("--volume must be between 0 and 1")
    if args.command == "run":
        if args.button not in ("power", "aux"):
            arg_parser.error("FRAME_HEV_BUTTON must be power or aux")
        if not 0 < args.debounce_ms < args.double_tap_ms < args.long_hold_ms or args.resume_guard_ms < 0:
            arg_parser.error("timings require 0 < debounce < double-tap < long-hold and nonnegative resume guard")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "doctor":
            return doctor(args)
        elif args.command == "run":
            run(args)
        else:
            percent = args.percent if args.percent is not None else read_battery()[1]
            words = phrase(percent)
            LOG.info("action=announce percent=%s phrase=%s", percent, " ".join(words))
            if args.dry_run:
                print(json.dumps({"percent": percent, "phrase": words}))
            else:
                path = compose_wave(args.assets, percent)
                subprocess.run(["pw-play", f"--volume={args.volume}", str(path)], check=True, timeout=30)
        return 0
    except (OSError, ValueError, RuntimeError, wave.Error, subprocess.SubprocessError) as error:
        LOG.error("%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
