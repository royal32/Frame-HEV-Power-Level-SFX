#!/usr/bin/env python3
"""Fetch or import the original HEV WAV clips listed in assets/manifest.json."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.error
import urllib.request
import wave


ROOT = Path(__file__).resolve().parents[1]


def verify_clip(data: bytes, clip: dict) -> None:
    """Reject HTML/error responses, changed downloads, and incompatible audio."""
    if len(data) != clip["bytes"]:
        raise ValueError(f"expected {clip['bytes']} bytes, received {len(data)}")
    if not clip.get("sha256"):
        raise ValueError("manifest is missing the recorded SHA-256")
    if hashlib.sha256(data).hexdigest() != clip["sha256"]:
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


def save_clip(destination: Path, data: bytes) -> None:
    """Publish validated bytes without exposing a partial file to another run."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}-", suffix=".part", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def acquire_clip(clip: dict, args) -> tuple[str, str, list[str]]:
    relative = Path(clip["path"])
    destination = args.output / relative
    warnings = []
    if args.check or (destination.exists() and not args.force):
        verify_clip(destination.read_bytes(), clip)
        return "verified", str(destination), warnings
    if args.source_dir:
        source = find_local(args.source_dir, clip["path"])
        data = source.read_bytes()
        verify_clip(data, clip)
        save_clip(destination, data)
        return "imported", str(source), warnings
    for cache in args.cache_dir:
        source = cache / relative
        try:
            data = source.read_bytes()
            verify_clip(data, clip)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, EOFError, wave.Error) as error:
            warnings.append(f"ignored cache {source}: {error}")
            continue
        save_clip(destination, data)
        return "cached", str(source), warnings
    if args.offline:
        detail = "; ".join(warnings)
        raise FileNotFoundError(
            "no verified local clip available; provide --cache-dir DIR or --source-dir DIR, "
            "or rerun without --offline" + (f" ({detail})" if detail else ""))
    request = urllib.request.Request(clip["url"], headers={"User-Agent": "Frame-HEV-Power-Level-SFX/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(clip["bytes"] + 1)
    verify_clip(data, clip)
    save_clip(destination, data)
    return "downloaded", clip["url"], warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "assets" / "manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "assets", help="destination containing fvox/")
    parser.add_argument("--source-dir", type=Path, help="import from an installed game's valve, sound, or fvox directory")
    parser.add_argument("--cache-dir", type=Path, action="append", default=[], help="reuse verified clips from DIR/fvox (repeatable)")
    parser.add_argument("--offline", action="store_true", help="use local audio only; never download")
    parser.add_argument("--check", action="store_true", help="verify existing files without network access")
    parser.add_argument("--force", action="store_true", help="replace existing files after validation")
    parser.add_argument("--quiet", action="store_true", help="show summary and errors, without per-clip progress")
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text())
    failures, counts = [], {}
    for clip in manifest["clips"]:
        relative = Path(clip["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe manifest path: {relative}")
    mode = "Checking" if args.check else "Preparing"
    if not args.quiet:
        print(f"{mode} {len(manifest['clips'])} original HEV clips for {args.output}")
    with ThreadPoolExecutor(max_workers=4) as workers:
        pending = {workers.submit(acquire_clip, clip, args): clip for clip in manifest["clips"]}
        for completed, future in enumerate(as_completed(pending), 1):
            relative = pending[future]["path"]
            try:
                status, source, warnings = future.result()
                for warning in warnings:
                    print(warning, file=sys.stderr)
                counts[status] = counts.get(status, 0) + 1
                if not args.quiet:
                    print(f"[{completed}/{len(pending)}] {status} {relative} ({source})")
            except (OSError, ValueError, EOFError, wave.Error, urllib.error.URLError) as error:
                failures.append(relative)
                print(f"[{completed}/{len(pending)}] {relative}: {error}", file=sys.stderr)
    if failures:
        print(f"Failed: {len(failures)} of {len(manifest['clips'])} clips", file=sys.stderr)
        return 1
    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    print(f"Ready: {len(manifest['clips'])} original HEV clips ({summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
