#!/usr/bin/env python3
"""Fetch or import the original HEV WAV clips listed in assets/manifest.json."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request
import wave


ROOT = Path(__file__).resolve().parents[1]


def verify_clip(data: bytes, clip: dict) -> None:
    """Reject HTML/error responses, changed downloads, and incompatible audio."""
    if len(data) != clip["bytes"]:
        raise ValueError(f"expected {clip['bytes']} bytes, received {len(data)}")
    if clip.get("sha256") and hashlib.sha256(data).hexdigest() != clip["sha256"]:
        raise ValueError("SHA-256 differs from the recorded original clip")
    with wave.open(io.BytesIO(data), "rb") as audio:
        actual = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype())
        expected = (clip["channels"], clip["sample_width_bytes"], clip["sample_rate_hz"], "NONE")
        if actual != expected:
            raise ValueError(f"expected PCM format {expected}, received {actual}")
        if audio.getnframes() != clip["frames"]:
            raise ValueError("WAV frame count differs from the manifest")
        if len(audio.readframes(audio.getnframes())) != audio.getnframes() * audio.getnchannels() * audio.getsampwidth():
            raise ValueError("truncated WAV audio")


def find_local(source: Path, clip_path: str) -> Path:
    candidates = (
        source / clip_path,
        source / "sound" / clip_path,
        source / "valve" / "sound" / clip_path,
        source / Path(clip_path).name,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"{clip_path} not found under {source}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "assets" / "manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "assets", help="destination containing fvox/")
    parser.add_argument("--source-dir", type=Path, help="import from an installed game's valve, sound, or fvox directory")
    parser.add_argument("--check", action="store_true", help="verify existing files without network access")
    parser.add_argument("--force", action="store_true", help="replace existing files after validation")
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text())
    failures = []
    for clip in manifest["clips"]:
        relative = Path(clip["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe manifest path: {relative}")
        destination = args.output / relative
        try:
            if args.check or (destination.exists() and not args.force):
                verify_clip(destination.read_bytes(), clip)
                print(f"verified {relative}")
                continue
            if args.source_dir:
                data = find_local(args.source_dir, clip["path"]).read_bytes()
            else:
                request = urllib.request.Request(clip["url"], headers={"User-Agent": "Frame-HEV-Power-Level-SFX/1.0"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = response.read(2 * 1024 * 1024)
            verify_clip(data, clip)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".wav.part")
            temporary.write_bytes(data)
            temporary.replace(destination)
            print(f"saved {relative}")
        except (OSError, ValueError, EOFError, wave.Error, urllib.error.URLError) as error:
            failures.append(str(relative))
            print(f"{relative}: {error}", file=sys.stderr)
    if failures:
        print(f"Failed: {len(failures)} of {len(manifest['clips'])} clips", file=sys.stderr)
        return 1
    print(f"Ready: {len(manifest['clips'])} original HEV clips")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
