"""Installer transactions exercised without touching services, network, or HOME."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("frame_hev_install", ROOT / "tools/install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class PreflightTests(unittest.TestCase):
    def invoke(self, result, *, bus_exists=True, inherited=None):
        with (mock.patch.object(installer.sys, "platform", "linux"),
              mock.patch.object(installer.os, "getuid", return_value=1000),
              mock.patch.object(installer.shutil, "which", return_value="/usr/bin/tool"),
              mock.patch.object(Path, "is_socket", return_value=bus_exists),
              mock.patch.dict(os.environ, inherited or {}, clear=True),
              mock.patch.object(installer, "systemctl", return_value=result) as service):
            try:
                installer.preflight()
            finally:
                self.environment = dict(os.environ)
                self.service_calls = service.call_args_list

    def state(self, *, load="loaded", active="active", sub="running", result="success", code=0):
        return subprocess.CompletedProcess([], code, stdout=(
            f"LoadState={load}\nActiveState={active}\nSubState={sub}\nResult={result}\n"), stderr="")

    def assert_read_only(self):
        self.assertTrue(self.service_calls)
        self.assertTrue(all(call.args[0] == "show" for call in self.service_calls))

    def test_active_native_service_uses_steam_session_even_with_inherited_desktop_bus(self):
        self.invoke(self.state(), inherited={
            "XDG_RUNTIME_DIR": "/tmp/other-session",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/desktop-private-bus",
        })
        self.assertEqual(self.environment["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(self.environment["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")
        self.assert_read_only()

    def test_active_native_service_without_inherited_session(self):
        self.invoke(self.state())
        self.assertEqual(self.environment["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(self.environment["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")

    def test_missing_bus_fails_before_service_query(self):
        with self.assertRaisesRegex(RuntimeError, "session bus was not found at /run/user/1000/bus"):
            self.invoke(self.state(), bus_exists=False)
        self.assertEqual(self.service_calls, [])

    def test_service_manager_error_preserves_diagnostic_instead_of_claiming_sleep(self):
        result = subprocess.CompletedProcess([], 1, stdout="", stderr="Failed to connect to bus: Permission denied\n")
        with self.assertRaisesRegex(RuntimeError, "Failed to connect to bus: Permission denied") as error:
            self.invoke(result)
        self.assertNotIn("Wake the Frame", str(error.exception))
        self.assert_read_only()

    def test_missing_native_unit_reports_firmware_difference(self):
        for code in (0, 1):
            with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, "no steamos-powerbuttond.service"):
                self.invoke(self.state(load="not-found", active="inactive", sub="dead", code=code))
            self.assert_read_only()

    def test_inactive_or_failed_service_reports_state_and_diagnostic_command(self):
        for active, sub, result in (("inactive", "dead", "success"), ("failed", "failed", "exit-code")):
            with self.subTest(active=active):
                with self.assertRaises(RuntimeError) as error:
                    self.invoke(self.state(active=active, sub=sub, result=result))
                self.assertIn(f"ActiveState={active}", str(error.exception))
                self.assertIn(f"Result={result}", str(error.exception))
                self.assertIn("status steamos-powerbuttond.service steamvr.service", str(error.exception))
                self.assert_read_only()

    def test_masked_service_is_not_accepted_even_if_still_running(self):
        with self.assertRaisesRegex(RuntimeError, "LoadState=masked"):
            self.invoke(self.state(load="masked"))
        self.assert_read_only()

    def test_empty_success_output_does_not_bypass_native_service_requirement(self):
        with self.assertRaisesRegex(RuntimeError, "ActiveState=unknown"):
            self.invoke(subprocess.CompletedProcess([], 0, stdout="", stderr=""))
        self.assert_read_only()


class FakeHost:
    def __init__(self, *, active=False, enabled=False, stage_failure=None, startup_failure=False):
        self.active = active
        self.enabled = enabled
        self.stage_failure = stage_failure
        self.startup_failure = startup_failure
        self.service_calls = []
        self.commands = []

    def command(self, args, *, check=True, capture=False):
        self.commands.append(tuple(map(str, args)))
        script = Path(args[1]).name
        phase = "fetch" if script == "fetch_sounds.py" else "doctor"
        if self.stage_failure == phase:
            raise RuntimeError(f"simulated {phase} failure")
        if phase == "fetch":
            output = Path(args[args.index("--output") + 1]) / "fvox"
            output.mkdir(parents=True)
            (output / "one.wav").write_bytes(b"verified fixture audio")
        return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    def systemctl(self, *args, check=True):
        self.service_calls.append(args)
        operation = args[0]
        status = 0
        if operation == "is-active":
            status = 0 if self.active else 3
        elif operation == "is-enabled":
            status = 0 if self.enabled else 1
        elif operation == "stop":
            self.active = False
        elif operation == "enable":
            self.enabled = True
            if "--now" in args:
                if self.startup_failure:
                    # Enabling can succeed even when the accompanying start fails.
                    self.startup_failure = False
                    raise RuntimeError("simulated new service startup failure")
                self.active = True
        elif operation == "disable":
            self.enabled = False
        elif operation == "start":
            self.active = True
        if status and check:
            raise RuntimeError(f"simulated systemctl {operation} status {status}")
        return subprocess.CompletedProcess(args, status, stdout="", stderr="")


class InstallTests(unittest.TestCase):
    def paths(self, home):
        return (home / ".local/share/frame-hev", home / ".config/systemd/user/frame-hev.service",
                home / ".local/bin/frame-hev", home / ".config/frame-hev/environment")

    def invoke(self, home, host):
        with (mock.patch.object(installer, "preflight"),
              mock.patch.object(installer, "command", side_effect=host.command),
              mock.patch.object(installer, "systemctl", side_effect=host.systemctl),
              contextlib.redirect_stdout(io.StringIO()),
              contextlib.redirect_stderr(io.StringIO())):
            installer.install(source=ROOT, home=home)

    def seed_old(self, home):
        app, unit, launcher, settings = self.paths(home)
        for path, data, mode in ((app / "frame_hev.py", b"old runtime", 0o600),
                                 (app / "VERSION", b"old-version\n", 0o644),
                                 (app / "custom.txt", b"keep old application", 0o640),
                                 (unit, b"old unit\n", 0o600),
                                 (launcher, b"old launcher\n", 0o755),
                                 (settings, b"FRAME_HEV_VOLUME=0.12\n", 0o600)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(mode)

    def snapshot(self, home):
        return {str(path.relative_to(home)): (path.read_bytes(), path.stat().st_mode & 0o777)
                for path in home.rglob("*") if path.is_file()}

    def assert_clean_stage(self, home):
        self.assertEqual(list((home / ".local/share").glob(".frame-hev-install-*")), [])

    def assert_native_untouched(self, host):
        self.assertFalse(any("steamos-powerbuttond.service" in call for call in host.service_calls))
        for call in host.service_calls:
            if call[0] not in ("daemon-reload",):
                self.assertIn(installer.UNIT, call)

    def test_fresh_install_writes_app_permissions_settings_and_enables(self):
        with tempfile.TemporaryDirectory() as temp:
            home, host = Path(temp), FakeHost()
            self.invoke(home, host)
            app, unit, launcher, settings = self.paths(home)
            self.assertEqual((app / "frame_hev.py").read_bytes(), (ROOT / "frame_hev.py").read_bytes())
            self.assertEqual((app / "assets/fvox/one.wav").read_bytes(), b"verified fixture audio")
            self.assertEqual(unit.read_bytes(), (ROOT / "systemd/frame-hev.service").read_bytes())
            self.assertEqual(launcher.read_bytes(), (ROOT / "tools/control.sh").read_bytes())
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)
            self.assertEqual(settings.stat().st_mode & 0o777, 0o600)
            self.assertEqual(settings.read_bytes(), (ROOT / "config/environment").read_bytes())
            self.assertTrue(host.active)
            self.assertTrue(host.enabled)
            self.assertIn(("enable", "--now", installer.UNIT), host.service_calls)
            self.assert_clean_stage(home)
            self.assert_native_untouched(host)

    def test_repeat_update_preserves_user_settings_and_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            home, host = Path(temp), FakeHost()
            self.invoke(home, host)
            settings = self.paths(home)[3]
            settings.write_bytes(b"FRAME_HEV_VOLUME=0.23\nFRAME_HEV_BUTTON=aux\n")
            settings.chmod(0o640)
            previous = settings.read_bytes(), settings.stat().st_mode & 0o777
            self.invoke(home, host)
            self.assertEqual((settings.read_bytes(), settings.stat().st_mode & 0o777), previous)
            self.assertTrue(host.active)
            self.assertTrue(host.enabled)
            self.assert_clean_stage(home)
            self.assert_native_untouched(host)

    def test_fetch_or_doctor_failure_leaves_old_installation_running_unchanged(self):
        for phase in ("fetch", "doctor"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                self.seed_old(home)
                previous = self.snapshot(home)
                host = FakeHost(active=True, enabled=True, stage_failure=phase)
                with self.assertRaisesRegex(RuntimeError, phase):
                    self.invoke(home, host)
                self.assertEqual(self.snapshot(home), previous)
                self.assertTrue(host.active)
                self.assertTrue(host.enabled)
                self.assertFalse(any(call[0] == "stop" for call in host.service_calls))
                self.assert_clean_stage(home)
                self.assert_native_untouched(host)

    def test_startup_failure_restores_old_app_files_and_service_state(self):
        for active, enabled in ((True, True), (False, False), (False, True), (True, False)):
            with self.subTest(active=active, enabled=enabled), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                self.seed_old(home)
                previous = self.snapshot(home)
                host = FakeHost(active=active, enabled=enabled, startup_failure=True)
                with self.assertRaisesRegex(RuntimeError, "startup failure"):
                    self.invoke(home, host)
                self.assertEqual(self.snapshot(home), previous)
                self.assertEqual(host.active, active)
                self.assertEqual(host.enabled, enabled)
                self.assert_clean_stage(home)
                self.assert_native_untouched(host)

    def test_first_install_startup_failure_removes_new_files_and_disables(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            host = FakeHost(startup_failure=True)
            with self.assertRaisesRegex(RuntimeError, "startup failure"):
                self.invoke(home, host)
            for path in self.paths(home):
                self.assertFalse(path.exists(), str(path))
            self.assertEqual(self.snapshot(home), {})
            self.assertFalse(host.active)
            self.assertFalse(host.enabled)
            self.assert_clean_stage(home)
            self.assert_native_untouched(host)


if __name__ == "__main__":
    unittest.main()
