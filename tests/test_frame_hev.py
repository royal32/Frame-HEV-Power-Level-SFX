import importlib.util
import ctypes
import json
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock
import wave

SPEC = importlib.util.spec_from_file_location("frame_hev", Path(__file__).resolve().parents[1] / "frame_hev.py")
hev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hev)


class GestureTests(unittest.TestCase):
    def setUp(self):
        self.g = hev.GestureController()

    def tap(self, start):
        return self.g.event(1, start) + self.g.event(0, start + .06)

    def test_single_delayed_until_window_expires(self):
        self.assertEqual(self.tap(1), [])
        self.assertEqual(self.g.tick(1.4), [])
        self.assertEqual(self.g.tick(1.411), ["short"])
        self.assertEqual(self.g.tick(2), [])

    def test_double_tap_announces_once(self):
        self.assertEqual(self.tap(1), [])
        self.assertEqual(self.tap(1.3), ["announce"])
        self.assertEqual(self.g.tick(3), [])

    def test_second_press_can_finish_after_window(self):
        self.tap(1)
        self.g.event(1, 1.4)
        self.assertEqual(self.g.tick(1.5), [])
        self.assertEqual(self.g.event(0, 1.6), ["announce"])

    def test_late_second_is_two_singles(self):
        self.tap(1)
        self.assertEqual(self.tap(1.5), ["short"])
        self.assertEqual(self.g.tick(2), ["short"])

    def test_triple_is_double_then_single(self):
        self.tap(1)
        self.assertEqual(self.tap(1.2), ["announce"])
        self.assertEqual(self.tap(1.4), [])
        self.assertEqual(self.g.tick(2), ["short"])

    def test_long_hold_and_repeats(self):
        self.g.event(1, 1)
        self.assertEqual(self.g.event(2, 1.5), [])
        self.assertEqual(self.g.tick(2), ["long"])
        self.assertEqual(self.g.event(2, 2.5), [])
        self.assertEqual(self.g.event(0, 3), [])
        self.assertEqual(self.g.tick(4), [])

    def test_tap_then_hold_prefers_long_without_suspending_first(self):
        self.tap(1)
        self.g.event(1, 1.2)
        self.assertEqual(self.g.tick(2.21), ["long"])
        self.assertEqual(self.g.event(0, 2.3), [])

    def test_bounce_pulses_and_duplicate_down(self):
        self.g.event(1, 1)
        self.g.event(1, 1.005)
        self.assertEqual(self.g.event(0, 1.01), [])
        self.assertEqual(self.g.tick(2), [])
        self.tap(3)
        self.g.event(1, 3.065)
        self.g.event(0, 3.07)
        self.assertEqual(self.g.tick(4), ["short"])

    def test_resume_or_dropped_event_reset_does_not_synthesize(self):
        self.tap(1)
        self.g.event(1, 1.2)
        self.g.reset()
        self.assertEqual(self.g.event(0, 3), [])
        self.assertEqual(self.g.tick(4), [])
        self.assertEqual(self.tap(5), [])
        self.assertEqual(self.g.tick(6), ["short"])

    def test_deadline_only_while_a_gesture_is_in_progress(self):
        self.assertIsNone(self.g.deadline())
        self.g.event(1, 1)
        self.assertEqual(self.g.deadline(), 2)
        self.g.event(0, 1.06)
        self.assertAlmostEqual(self.g.deadline(), 1.41)
        self.assertEqual(self.g.tick(self.g.deadline()), ["short"])
        self.assertIsNone(self.g.deadline())
        self.g.event(1, 3)
        self.assertEqual(self.g.tick(4), ["long"])
        self.assertIsNone(self.g.deadline())


class BatteryTests(unittest.TestCase):
    def battery(self, root, name, percent, scope=None, kind="Battery"):
        path = root / name
        path.mkdir()
        (path / "type").write_text(kind)
        (path / "capacity").write_text(str(percent))
        if scope:
            (path / "scope").write_text(scope)
        return path

    def test_controller_excluded_and_system_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.battery(root, "controller", 100, "Device")
            system = self.battery(root, "max1720x_bat_7-36", 63, "System")
            self.assertEqual(hev.read_battery(root), (system, 63))

    def test_invalid_and_ambiguous_batteries_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.battery(root, "bad", 101)
            self.battery(root, "adapter", 10, kind="Mains")
            with self.assertRaises(RuntimeError):
                hev.read_battery(root)
            self.battery(root, "system1", 44)
            self.battery(root, "system2", 55)
            with self.assertRaises(RuntimeError):
                hev.read_battery(root)


class SteamVRBatteryTests(unittest.TestCase):
    def library(self, fraction=0.45, *, init_error=0, property_error=0, interface_error=0):
        error_pointer = ctypes.POINTER(ctypes.c_int)
        def initialize(error, application):
            self.assertEqual(application, 3)
            ctypes.cast(error, error_pointer)[0] = init_error
            return 1

        def get_float(device, prop, error):
            self.assertEqual((device, prop), (0, 1012))
            error[0] = property_error
            return fraction

        callback = ctypes.CFUNCTYPE(ctypes.c_float, ctypes.c_uint32, ctypes.c_int, error_pointer)(get_float)
        table = (ctypes.c_void_p * 24)()
        table[23] = ctypes.cast(callback, ctypes.c_void_p).value

        def get_interface(name, error):
            self.assertEqual(name, b"FnTable:IVRSystem_026")
            ctypes.cast(error, error_pointer)[0] = interface_error
            return ctypes.addressof(table) if not interface_error else None

        library = mock.Mock()
        library.VR_InitInternal.side_effect = initialize
        library.VR_GetGenericInterface.side_effect = get_interface
        # Keep ctypes objects alive for the native callback during the test.
        library.fixture_table, library.fixture_callback = table, callback
        return library

    def test_hmd_percent_uses_nearest_integer_including_float_representation(self):
        for fraction, expected in ((0.45, 45), (0.46, 46), (0.51, 51), (0, 0), (1, 100)):
            library = self.library(fraction)
            with self.subTest(fraction=fraction), mock.patch.object(hev.ctypes, "CDLL", return_value=library):
                self.assertEqual(hev.openvr_battery_percent(), expected)
                library.VR_ShutdownInternal.assert_called_once()

    def test_invalid_properties_are_rejected_and_connection_closed(self):
        for fraction, error in ((float("nan"), 0), (float("inf"), 0), (-0.1, 0), (1.1, 0), (0.45, 4)):
            library = self.library(fraction, property_error=error)
            with self.subTest(fraction=fraction, error=error), mock.patch.object(hev.ctypes, "CDLL", return_value=library):
                with self.assertRaisesRegex(RuntimeError, "invalid HMD battery"):
                    hev.openvr_battery_percent()
                library.VR_ShutdownInternal.assert_called_once()

    def test_init_failure_does_not_use_function_table(self):
        library = self.library(init_error=121)
        with mock.patch.object(hev.ctypes, "CDLL", return_value=library):
            with self.assertRaisesRegex(RuntimeError, "connection failed"):
                hev.openvr_battery_percent()
        library.VR_GetGenericInterface.assert_not_called()
        library.VR_ShutdownInternal.assert_not_called()

    def test_interface_mismatch_is_rejected_and_connection_closed(self):
        library = self.library(interface_error=105)
        with mock.patch.object(hev.ctypes, "CDLL", return_value=library):
            with self.assertRaisesRegex(RuntimeError, "IVRSystem_026 is unavailable"):
                hev.openvr_battery_percent()
        library.VR_ShutdownInternal.assert_called_once()

    def test_prefers_fresh_steamvr_level_over_raw_kernel_capacity(self):
        with (mock.patch.object(hev.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, stdout=json.dumps({"percent": 51})),
                subprocess.CompletedProcess([], 0, stdout=json.dumps({"percent": 50})),
              ]) as query,
              mock.patch.object(hev, "read_sysfs_battery", return_value=(Path("/battery"), 46)) as kernel):
            self.assertEqual(hev.read_battery(), ("openvr:hmd", 51))
            self.assertEqual(hev.read_battery(), ("openvr:hmd", 50))
            self.assertEqual(query.call_args.kwargs["timeout"], 2)
            self.assertTrue(query.call_args.kwargs["check"])
            kernel.assert_not_called()

    def test_probe_timeout_or_crash_falls_back_without_interrupting_button_handling(self):
        for error in (subprocess.TimeoutExpired("probe", 2), subprocess.CalledProcessError(-11, "probe"), OSError("missing")):
            with (self.subTest(error=type(error)), mock.patch.object(hev.subprocess, "run", side_effect=error),
                  mock.patch.object(hev, "read_sysfs_battery", return_value=(Path("/battery"), 46)),
                  self.assertLogs("frame-hev", level="WARNING")):
                self.assertEqual(hev.read_battery(), (Path("/battery"), 46))

    def test_bad_probe_output_falls_back(self):
        for output in ("not json", "[]", '{"percent":null}', '{"percent":true}', '{"percent":101}', '{"percent":-1}'):
            with (self.subTest(output=output), mock.patch.object(hev.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=output)),
                  mock.patch.object(hev, "read_sysfs_battery", return_value=(Path("/battery"), 46)),
                  self.assertLogs("frame-hev", level="WARNING")):
                self.assertEqual(hev.read_battery(), (Path("/battery"), 46))


class AudioTests(unittest.TestCase):
    def assets(self, root):
        names = sorted({name for n in range(101) for name in hev.phrase(n)})
        samples = {}
        for i, name in enumerate(names, 1):
            samples[name] = bytes([i, 0]) * 10
            with wave.open(str(root / (name + ".wav")), "wb") as target:
                target.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
                target.writeframes(samples[name])
        return samples

    def test_exact_phrases(self):
        self.assertEqual(hev.phrase(0), ["armor_gone"])
        self.assertEqual(hev.phrase(25), ["power_level_is", "twentyfive", "percent"])
        self.assertEqual(hev.phrase(41), ["power_level_is", "fourty", "one", "percent"])
        self.assertEqual(hev.phrase(100), ["power_level_is", "onehundred", "percent"])
        for n in (-1, 101):
            with self.assertRaises(ValueError):
                hev.phrase(n)

    def test_all_percentages_preserve_pcm_in_order_and_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            samples = self.assets(root)
            self.assertEqual(hev.validate_assets(root)["sample_rate"], 22050)
            for percent in range(101):
                output = hev.compose_wave(root, percent, root / "cache")
                fmt, frames = hev.read_pcm(output)
                self.assertEqual(fmt, (1, 2, 22050))
                self.assertEqual(frames, b"".join(samples[n] for n in hev.phrase(percent)))
                self.assertEqual(output, hev.compose_wave(root, percent, root / "cache"))

    def test_mismatched_assets_rejected_and_content_changes_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assets(root)
            first = hev.compose_wave(root, 1, root / "cache")
            with wave.open(str(root / "one.wav"), "wb") as target:
                target.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
                target.writeframes(b"\x7f\x00" * 10)
            self.assertNotEqual(first, hev.compose_wave(root, 1, root / "cache"))
            with wave.open(str(root / "percent.wav"), "wb") as target:
                target.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
                target.writeframes(b"\0" * 40)
            with self.assertRaises(ValueError):
                hev.compose_wave(root, 1, root / "cache")
            with self.assertRaises(ValueError):
                hev.validate_assets(root)


class DeviceTests(unittest.TestCase):
    def test_resolves_by_name_and_refuses_other_keys(self):
        inventory = [{"path": "/dev/input/event8", "name": "pmic_pwrkey", "keys": [116]}]
        with mock.patch.object(hev, "input_inventory", return_value=inventory):
            self.assertEqual(hev.resolve_device("power")["path"], "/dev/input/event8")
            inventory[0]["keys"].append(115)
            with self.assertRaises(RuntimeError):
                hev.resolve_device("power")

    def test_aux_can_share_volume_key(self):
        with mock.patch.object(hev, "input_inventory", return_value=[
                {"path": "/dev/input/event2", "name": "gpio-keys", "keys": [115, 353]}]):
            self.assertEqual(hev.resolve_device("aux")["name"], "gpio-keys")


class RuntimeSafetyTests(unittest.TestCase):
    def test_aux_never_dispatches_steam_and_audio_queue_is_bounded(self):
        args = SimpleNamespace(button="aux", dry_run=False)
        with mock.patch.object(hev.threading, "Thread"):
            runner = hev.ActionRunner(args)
        runner.submit("short")
        runner.submit("long")
        self.assertTrue(runner.steam.empty())
        self.assertTrue(runner.audio.empty())
        with mock.patch.object(hev, "suspend_offset", return_value=0):
            runner.submit("announce")
            with self.assertLogs("frame-hev", level="WARNING"):
                runner.submit("announce")
        self.assertEqual(runner.audio.qsize(), 1)
        self.assertEqual(runner.audio.get_nowait(), ("announce", 0))

    def runner(self):
        with mock.patch.object(hev.threading, "Thread"):
            return hev.ActionRunner(SimpleNamespace(button="power", dry_run=False, steam=Path("/steam")))

    def test_queued_power_before_suspend_is_ignored_and_fresh_power_dispatches(self):
        runner = self.runner()
        with mock.patch.object(hev, "suspend_offset", side_effect=[0, 10]):
            runner.submit("short")
            runner.submit("long")
        # The worker drains two actions after a ten-second suspend.
        with (mock.patch.object(runner.stop, "is_set", side_effect=[False, False, True]),
              mock.patch.object(hev, "suspend_offset", return_value=10),
              mock.patch.object(hev, "preparing_for_sleep", return_value=False) as preparing,
              mock.patch.object(runner, "execute") as execute):
            runner.work(runner.steam)
        preparing.assert_called_once()
        execute.assert_called_once_with(["/steam", "-ifrunning", "steam://longpowerpress"], 5, 10)
        self.assertEqual(runner.steam.unfinished_tasks, 0)

    def test_suspend_during_sleep_query_discards_power_action(self):
        runner = self.runner()
        runner.steam.put(("short", 0))
        with (mock.patch.object(runner.stop, "is_set", side_effect=[False, True]),
              mock.patch.object(hev, "suspend_offset", side_effect=[0, 10]),
              mock.patch.object(hev, "preparing_for_sleep", return_value=False),
              mock.patch.object(runner, "execute") as execute):
            runner.work(runner.steam)
        execute.assert_not_called()
        self.assertEqual(runner.steam.unfinished_tasks, 0)

    def test_close_unblocks_idle_workers(self):
        runner = hev.ActionRunner(SimpleNamespace(button="power", dry_run=False, steam=Path("/steam")))
        runner.close()
        self.assertFalse(any(thread.is_alive() for thread in runner.threads))

    def test_final_pre_spawn_check_discards_action_after_resume(self):
        runner = self.runner()
        with (mock.patch.object(hev, "suspend_offset", return_value=10),
              mock.patch.object(hev.subprocess, "Popen") as popen):
            runner.execute(["/steam", "-ifrunning", "steam://shortpowerpress"], 5, 0)
        popen.assert_not_called()

    def test_suspend_inside_select_discards_wake_events_before_dispatch(self):
        # select begins before suspend, then returns queued wake button events.
        # The second clock check must reset the gesture before consuming them.
        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(button="power", dry_run=True, device=None,
                                   device_name=None, lock_file=Path(temp) / "runtime.lock",
                                   double_tap_ms=350, long_hold_ms=1000, debounce_ms=25,
                                   resume_guard_ms=1000)
            wake_events = b"".join(hev.EVENT.pack(seconds, micros, hev.EV_KEY, hev.KEY_POWER, value)
                                   for seconds, micros, value in [(60, 0, 1), (60, 60000, 0),
                                                                  (60, 200000, 1), (60, 260000, 0)])
            with (mock.patch.object(hev.Path, "home", return_value=Path(temp)),
                  mock.patch.object(hev, "resolve_device", return_value={"path": "fake", "name": "pmic_pwrkey"}),
                  mock.patch.object(hev, "get_bits", return_value=set()),
                  mock.patch.object(hev.os, "pipe2", return_value=(97, 98)),
                  mock.patch.object(hev.os, "open", return_value=99),
                  mock.patch.object(hev.os, "close") as close,
                  mock.patch.object(hev.os, "read", return_value=wake_events),
                  mock.patch.object(hev.fcntl, "ioctl"),
                  mock.patch.object(hev.fcntl, "flock"),
                  mock.patch.object(hev.time, "CLOCK_BOOTTIME", 7, create=True),
                  mock.patch.object(hev.time, "monotonic", side_effect=[1, 1, 60, 60]),
                  mock.patch.object(hev.time, "clock_gettime", side_effect=[1, 1, 90, 90]),
                  mock.patch.object(hev.select, "select", side_effect=[([99], [], []), KeyboardInterrupt]) as sel,
                  mock.patch.object(hev.signal, "signal"),
                  mock.patch.object(hev, "notify_ready"),
                  mock.patch.object(hev, "ActionRunner") as runner_class):
                runner = runner_class.return_value
                runner.errors.empty.return_value = True
                with self.assertRaises(KeyboardInterrupt):
                    hev.run(args)
                runner.submit.assert_not_called()
                # Idle: block on the button and the wake pipe with no timeout.
                self.assertEqual(sel.call_args_list[0].args, ([99, 97], [], [], None))
                self.assertEqual(close.call_args_list, [mock.call(99), mock.call(97), mock.call(98)])
                runner.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
