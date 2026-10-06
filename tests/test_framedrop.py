"""FrameDrop packaging and first-launch behavior without touching the host."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load("release", "build_release.py")
setup = load("setup", "framedrop.py")
entry = load("entry", "framedrop-entry.py")


class PackageTests(unittest.TestCase):
    def launcher(self, directory, machine=183):
        # A header fixture, not a runnable program. Real launcher execution is
        # checked separately on the Frame.
        data = bytearray(64)
        data[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<HH", data, 16, 3, machine)
        path = directory / "launcher"
        path.write_bytes(data)
        return path

    def test_manifest_hash_and_layout_match_downloadable_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            files = release.files_for_release()
            artifacts = release.build(temp / "dist", "0.1.1", files, framedrop_launcher=self.launcher(temp))
            self.assertEqual(len(artifacts), 6)
            manifest = json.loads((temp / "dist/frame-hev.framedrop.json").read_text())
            bundle = temp / "dist/frame-hev-0.1.1-linux-arm64.zip"
            native = temp / "dist/frame-hev-0.1.1-linux-arm64.bin"
            self.assertEqual(manifest["schema"], "framedrop.install/v1")
            self.assertEqual(manifest["name"], "Frame HEV")
            self.assertEqual(manifest["files"], [{
                "url": "https://github.com/royal32/Frame-HEV-Power-Level-SFX/releases/download/v0.1.1/" + native.name,
                "sha256": hashlib.sha256(native.read_bytes()).hexdigest(),
            }])
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(archive.namelist(), ["frame-hev-setup"])
                self.assertEqual(archive.read("frame-hev-setup"), native.read_bytes())
                executable = [item.filename for item in archive.infolist() if (item.external_attr >> 16) & 0o111]
                self.assertEqual(executable, ["frame-hev-setup"])
            with zipfile.ZipFile(native) as archive:
                self.assertEqual(archive.namelist(), ["__main__.py"] + [
                    f"payload/{path.relative_to(ROOT).as_posix()}" for path in files])
                self.assertTrue(all(stat.S_ISREG(item.external_attr >> 16) for item in archive.infolist()))
                self.assertEqual(archive.read("__main__.py"), (ROOT / "tools/framedrop-entry.py").read_bytes())
                self.assertIn("payload/tools/framedrop.py", archive.namelist())
                self.assertIn("payload/tools/framedrop-launcher.c", archive.namelist())
                self.assertFalse(any(name.lower().endswith(".wav") or "AGENTS.md" in name for name in archive.namelist()))
            self.assertEqual(native.read_bytes()[:4], b"\x7fELF")
            checksums = (temp / "dist/SHA256SUMS").read_text()
            for path in artifacts[:-1]:
                self.assertIn(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n", checksums)

    def test_all_artifacts_are_reproducible(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            launcher = self.launcher(temp)
            first = release.build(temp / "a", "0.1.1", release.files_for_release(), framedrop_launcher=launcher)
            second = release.build(temp / "b", "0.1.1", release.files_for_release(), framedrop_launcher=launcher)
            self.assertEqual([(p.name, p.read_bytes()) for p in first], [(p.name, p.read_bytes()) for p in second])

    def test_wrong_architecture_fails_before_writing_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with self.assertRaisesRegex(ValueError, "AArch64 ELF"):
                release.build(temp / "dist", "0.1.1", release.files_for_release(), framedrop_launcher=self.launcher(temp, 62))
            self.assertFalse((temp / "dist").exists())

    def test_source_only_release_keeps_its_existing_three_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            artifacts = release.build(Path(temp), "0.1.1", release.files_for_release())
            self.assertEqual([p.name for p in artifacts], ["frame-hev-0.1.1.zip", "frame-hev-0.1.1.tar.gz", "SHA256SUMS"])

    def test_renamed_extensionless_native_still_runs_the_python_entry_point(self):
        # GitHub redirects use an identifier as the final URL basename. Check
        # both the ELF magic FrameDrop tests and Python's embedded ZIP dispatch.
        with tempfile.TemporaryDirectory(prefix="hev package with spaces ") as temp:
            temp = Path(temp)
            release.build(temp / "dist", "0.1.2", release.files_for_release(),
                          framedrop_launcher=self.launcher(temp))
            renamed = temp / "000ec105-02d8-4147-bccd-67bd7f41ff35"
            (temp / "dist/frame-hev-0.1.2-linux-arm64.bin").rename(renamed)
            self.assertEqual(renamed.read_bytes()[:4], b"\x7fELF")
            result = subprocess.run([setup.sys.executable, str(renamed), "--help"],
                                    cwd=temp, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--no-ui", result.stdout)
            self.assertIn("--action", result.stdout)

    def test_embedded_entry_retains_payload_until_child_finishes_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            release.build(temp / "dist", "0.1.2", release.files_for_release(),
                          framedrop_launcher=self.launcher(temp))
            native = temp / "dist/frame-hev-0.1.2-linux-arm64.bin"
            extracted = []

            def child(command, **kwargs):
                source = kwargs["cwd"]
                extracted.append(source)
                self.assertEqual(command, [setup.sys.executable, str(source / "tools/framedrop.py"),
                                           "--no-ui", "--action", "uninstall"])
                self.assertEqual((source / "VERSION").read_bytes(), (ROOT / "VERSION").read_bytes())
                self.assertTrue((source / "tools/uninstall.sh").is_file())
                return subprocess.CompletedProcess(command, 7)

            with (mock.patch.object(entry.sys, "argv", [str(native), "--no-ui", "--action", "uninstall"]),
                  mock.patch.object(entry.subprocess, "run", side_effect=child)):
                self.assertEqual(entry.main(), 7)
            self.assertFalse(extracted[0].parent.exists())


class HeadsetSetupTests(unittest.TestCase):
    def response(self, code=0, output="", error=""):
        return subprocess.CompletedProcess([], code, stdout=output, stderr=error)

    def test_first_launch_install_and_cancel(self):
        for code, action in ((0, "install"), (1, None)):
            with self.subTest(code=code), mock.patch.object(setup, "dialog", return_value=self.response(code)):
                self.assertEqual(setup.choose_action(False), action)

    def test_existing_install_can_update_uninstall_or_cancel(self):
        for code, output, action in ((0, "Install / Update\n", "install"), (0, "Uninstall\n", "uninstall"), (1, "", None)):
            with self.subTest(action=action), mock.patch.object(setup, "dialog", return_value=self.response(code, output)):
                self.assertEqual(setup.choose_action(True), action)

    def test_display_failure_is_not_treated_as_install_confirmation(self):
        with mock.patch.object(setup, "dialog", return_value=self.response(2, error="Cannot open display")):
            with self.assertRaisesRegex(RuntimeError, "Cannot open display"):
                setup.choose_action(False)

    def test_unknown_menu_result_is_reported_instead_of_silently_cancelling(self):
        with mock.patch.object(setup, "dialog", return_value=self.response(0, "unexpected")):
            with self.assertRaisesRegex(RuntimeError, "unknown selection"):
                setup.choose_action(True)

    def invoke(self, temp, action, code, show_ui):
        def run(command, **kwargs):
            kwargs["stdout"].write("installer diagnostic\n")
            self.child = (command, kwargs)
            return self.response(code)

        with (mock.patch.object(setup.subprocess, "run", side_effect=run),
              mock.patch.object(setup.subprocess, "Popen") as progress,
              mock.patch.object(setup, "dialog") as dialogs,
              mock.patch.object(setup.os, "getuid", return_value=1000),
              mock.patch.object(Path, "is_socket", return_value=True),
              mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/tmp/wrong", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/wrong"}),
              contextlib.redirect_stdout(io.StringIO())):
            progress.return_value.poll.return_value = None
            result = setup.run_action(action, source=ROOT, home=temp, show_ui=show_ui)
            self.dialogs = dialogs.call_args_list
            self.progress = progress
            return result

    def test_success_uses_local_installer_and_keeps_service_outside_library_process(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self.invoke(Path(temp), "install", 0, True), 0)
            command, kwargs = self.child
            self.assertEqual(command, [setup.sys.executable, str(ROOT / "tools/install.py")])
            self.assertEqual(kwargs["env"]["XDG_RUNTIME_DIR"], "/run/user/1000")
            self.assertEqual(kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")
            log = Path(temp) / ".local/state/frame-hev/install.log"
            self.assertIn("installer diagnostic", log.read_text())
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.dialogs[0].args[0], "--info")
            self.progress.return_value.terminate.assert_called_once()
            self.progress.return_value.wait.assert_called_once()
            self.progress.return_value.stdin.close.assert_called_once()

    def test_failed_install_shows_error_log_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self.invoke(Path(temp), "install", 1, True), 1)
            self.assertEqual([call.args[0] for call in self.dialogs], ["--error", "--text-info"])
            self.assertIn(f"--filename={temp}/.local/state/frame-hev/install.log", self.dialogs[1].args)

    def test_uninstall_uses_existing_remover_and_retains_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self.invoke(Path(temp), "uninstall", 0, False), 0)
            self.assertEqual(self.child[0], ["bash", str(ROOT / "tools/uninstall.sh")])
            self.progress.assert_not_called()
            self.assertEqual(self.dialogs, [])


if __name__ == "__main__":
    unittest.main()
