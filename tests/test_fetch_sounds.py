import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave


SPEC = importlib.util.spec_from_file_location("fetch_sounds", Path(__file__).resolve().parents[1] / "tools" / "fetch_sounds.py")
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)


class FetchSoundsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "output"
        self.manifest = self.root / "manifest.json"
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setparams((1, 1, 11025, 100, "NONE", "not compressed"))
            audio.writeframes(bytes([128]) * 100)
        self.data = buffer.getvalue()
        self.clips = [self.clip("one"), self.clip("two")]
        self.write_manifest()

    def clip(self, name):
        return {"path": f"fvox/{name}.wav", "url": f"https://example.test/{name}.wav",
                "bytes": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest(),
                "channels": 1, "sample_width_bytes": 1, "sample_rate_hz": 11025, "frames": 100}

    def write_manifest(self):
        self.manifest.write_text(json.dumps({"clips": self.clips}))

    def write_clip(self, directory, clip, data=None):
        path = directory / clip["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.data if data is None else data)
        return path

    def run_main(self, *arguments):
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            return fetch.main(["--manifest", str(self.manifest), "--output", str(self.output), *map(str, arguments)])

    def assert_ready(self):
        for clip in self.clips:
            fetch.verify_clip((self.output / clip["path"]).read_bytes(), clip)
        self.assertEqual(list(self.output.rglob("*.part")), [])

    def test_fresh_online_downloads_are_bounded_to_four(self):
        self.clips = [self.clip(str(number)) for number in range(8)]
        self.write_manifest()
        barrier, lock = threading.Barrier(4), threading.Lock()
        active = 0
        peak = 0

        class Response(io.BytesIO):
            def __enter__(response):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(active, peak)
                barrier.wait(timeout=3)
                return response

            def __exit__(response, *args):
                nonlocal active
                with lock:
                    active -= 1
                return super().__exit__(*args)

        with patch.object(fetch.urllib.request, "urlopen", side_effect=lambda *args, **kwargs: Response(self.data)) as network:
            self.assertEqual(self.run_main(), 0)
        self.assertEqual(network.call_count, 8)
        self.assertEqual(peak, 4)
        self.assertIn("8 downloaded", self.stdout.getvalue())
        self.assert_ready()

    def test_verified_cache_and_existing_output_work_offline(self):
        cache = self.root / "cache"
        self.write_clip(self.output, self.clips[0])
        self.write_clip(cache, self.clips[1])
        with patch.object(fetch.urllib.request, "urlopen") as network:
            self.assertEqual(self.run_main("--offline", "--cache-dir", cache), 0)
        network.assert_not_called()
        self.assertIn("1 cached", self.stdout.getvalue())
        self.assertIn("1 verified", self.stdout.getvalue())
        self.assert_ready()

    def test_corrupt_cache_is_ignored_then_next_cache_used(self):
        bad, good = self.root / "bad", self.root / "good"
        for clip in self.clips:
            self.write_clip(bad, clip, b"broken")
            self.write_clip(good, clip)
        with patch.object(fetch.urllib.request, "urlopen") as network:
            self.assertEqual(self.run_main("--offline", "--cache-dir", bad, "--cache-dir", good), 0)
        network.assert_not_called()
        self.assertIn("ignored cache", self.stderr.getvalue())
        self.assert_ready()

    def test_corrupt_cache_falls_back_to_network_when_online(self):
        cache = self.root / "cache"
        for clip in self.clips:
            self.write_clip(cache, clip, b"broken")
        with patch.object(fetch.urllib.request, "urlopen", side_effect=lambda *args, **kwargs: io.BytesIO(self.data)) as network:
            self.assertEqual(self.run_main("--cache-dir", cache), 0)
        self.assertEqual(network.call_count, 2)
        self.assert_ready()

    def test_missing_offline_clip_reports_actionable_error(self):
        with patch.object(fetch.urllib.request, "urlopen") as network:
            self.assertEqual(self.run_main("--offline"), 1)
        network.assert_not_called()
        self.assertIn("--cache-dir", self.stderr.getvalue())
        self.assertIn("without --offline", self.stderr.getvalue())

    def test_source_import_never_falls_back_to_network(self):
        source = self.root / "game"
        for clip in self.clips:
            self.write_clip(source / "valve" / "sound", clip)
        with patch.object(fetch.urllib.request, "urlopen") as network:
            self.assertEqual(self.run_main("--source-dir", source), 0)
            (self.output / self.clips[1]["path"]).unlink()
            (source / "valve" / "sound" / self.clips[1]["path"]).unlink()
            self.assertEqual(self.run_main("--source-dir", source), 1)
        network.assert_not_called()
        self.assertIn("not found under", self.stderr.getvalue())

    def test_check_validates_output_only_without_repair(self):
        cache = self.root / "cache"
        for clip in self.clips:
            self.write_clip(cache, clip)
        with patch.object(fetch.urllib.request, "urlopen") as network:
            self.assertEqual(self.run_main("--check", "--cache-dir", cache), 1)
        network.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_corrupt_forced_download_preserves_existing_file(self):
        old = {}
        for clip in self.clips:
            path = self.write_clip(self.output, clip)
            old[path] = path.read_bytes()
        with patch.object(fetch.urllib.request, "urlopen", side_effect=lambda *args, **kwargs: io.BytesIO(b"bad download")):
            self.assertEqual(self.run_main("--force"), 1)
        for path, data in old.items():
            self.assertEqual(path.read_bytes(), data)
        self.assert_ready()

    def test_existing_corruption_requires_force_and_is_not_overwritten(self):
        for clip in self.clips:
            self.write_clip(self.output, clip, b"old invalid file")
        with patch.object(fetch.urllib.request, "urlopen") as network:
            self.assertEqual(self.run_main(), 1)
        network.assert_not_called()
        self.assertEqual((self.output / self.clips[0]["path"]).read_bytes(), b"old invalid file")

    def test_byte_hash_pcm_and_frame_validation(self):
        clip = self.clips[0]
        with self.assertRaisesRegex(ValueError, "bytes"):
            fetch.verify_clip(self.data[:-1], clip)
        with self.assertRaisesRegex(ValueError, "SHA-256 differs"):
            fetch.verify_clip(self.data[:-1] + bytes([self.data[-1] ^ 1]), clip)
        with self.assertRaisesRegex(ValueError, "missing.*SHA-256"):
            fetch.verify_clip(self.data, {key: value for key, value in clip.items() if key != "sha256"})
        with self.assertRaisesRegex(ValueError, "PCM format"):
            fetch.verify_clip(self.data, {**clip, "sample_rate_hz": 22050})
        with self.assertRaisesRegex(ValueError, "frame count"):
            fetch.verify_clip(self.data, {**clip, "frames": 101})


if __name__ == "__main__":
    unittest.main()
