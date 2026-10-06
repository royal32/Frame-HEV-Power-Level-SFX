#!/usr/bin/env python3
"""Build reproducible, audio-free release archives with Python's standard library."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import stat
import struct
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = (
    'VERSION', 'README.md', 'LICENSE', 'install.sh', 'install.ps1', 'frame_hev.py',
    'assets/README.md', 'assets/manifest.json', 'config/environment',
    'systemd/frame-hev.service', 'tools/install.sh', 'tools/install.py', 'tools/uninstall.sh',
    'tools/control.sh', 'tools/fetch_sounds.py', 'tools/build_release.py',
    'tools/framedrop.py', 'tools/framedrop-launcher.c', 'tools/framedrop-entry.py',
    'assets/framedrop-button.svg',
)
OPTIONAL = (
    'LICENSE.md', 'docs/frame-development.md', 'docs/validation.md',
    'tools/frame-ui.mjs', 'tools/smoke_input.py', 'tests/test_frame_hev.py',
    'tests/test_fetch_sounds.py', 'tests/test_install.py', 'tests/test_framedrop.py',
    'docs/framedrop.md',
)
RELEASE_URL = 'https://github.com/royal32/Frame-HEV-Power-Level-SFX/releases/download'
FRAMEDROP_MANIFEST = 'frame-hev.framedrop.json'
# Fixed metadata makes identical source trees produce identical archives.
EPOCH = 315532800  # 1980-01-01 UTC, also the earliest ZIP timestamp.


def files_for_release() -> list[Path]:
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    if missing:
        raise ValueError('Missing release files: ' + ', '.join(missing))
    names = sorted(set(REQUIRED) | {name for name in OPTIONAL if (ROOT / name).is_file()})
    for name in names:
        path = ROOT / name
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f'Release files must be regular files inside the repository: {name}')
    return [ROOT / name for name in names]


def file_mode(path: Path) -> int:
    return 0o755 if path.suffix in {'.sh', '.ps1', '.py', '.mjs'} else 0o644


def validate_launcher(launcher: Path) -> bytes:
    """Reject the wrong platform before publishing a native FrameDrop bundle."""
    if launcher.is_symlink() or not launcher.is_file():
        raise ValueError('FrameDrop launcher must be a regular Linux ARM64 executable')
    data = launcher.read_bytes()
    if (len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01'
            or struct.unpack_from('<HH', data, 16) not in ((2, 183), (3, 183))):
        raise ValueError('FrameDrop launcher must be a 64-bit little-endian AArch64 ELF executable')
    return data


def write_zip(path: Path, entries: list[tuple[str, bytes, int]], *, prefix: bytes = b'') -> None:
    # Appending a ZIP to ELF produces a native executable that Python can also
    # run as a zipapp. Keep offsets relative to the complete file.
    path.write_bytes(prefix)
    with zipfile.ZipFile(path, 'a', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)


def build(output: Path, version: str, files: list[Path], *, framedrop_launcher: Path | None = None) -> list[Path]:
    launcher_data = validate_launcher(framedrop_launcher) if framedrop_launcher else None
    output.mkdir(parents=True, exist_ok=True)
    prefix = f'frame-hev-{version}'
    zip_path = output / f'{prefix}.zip'
    tar_path = output / f'{prefix}.tar.gz'
    contents = [(path, path.read_bytes()) for path in files]
    write_zip(zip_path, [(f'{prefix}/{path.relative_to(ROOT).as_posix()}', data, file_mode(path))
                         for path, data in contents])
    with tar_path.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=EPOCH, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
                for path, data in contents:
                    info = tarfile.TarInfo(f'{prefix}/{path.relative_to(ROOT).as_posix()}')
                    info.size = len(data)
                    info.mode = file_mode(path)
                    info.mtime = EPOCH
                    archive.addfile(info, io.BytesIO(data))
    artifacts = [zip_path, tar_path]
    if launcher_data is not None:
        # FrameDrop 1.0.37 loses the .zip extension on GitHub release redirects.
        # Its ELF detection uses magic bytes, so the manifest uses a standalone
        # ELF+ZIP containing all source and an extraction entry point.
        native = output / f'{prefix}-linux-arm64.bin'
        entries = [('__main__.py', (ROOT / 'tools/framedrop-entry.py').read_bytes(), 0o644)]
        entries.extend((f'payload/{path.relative_to(ROOT).as_posix()}', data, 0o644) for path, data in contents)
        write_zip(native, entries, prefix=launcher_data)
        native.chmod(0o755)
        # Retain a ZIP for manual drag-and-drop. There is exactly one file to
        # launch and it is independent of extraction location and filename.
        framedrop_zip = output / f'{prefix}-linux-arm64.zip'
        write_zip(framedrop_zip, [('frame-hev-setup', native.read_bytes(), 0o755)])
        manifest = output / FRAMEDROP_MANIFEST
        manifest.write_text(json.dumps({
            'schema': 'framedrop.install/v1', 'name': 'Frame HEV',
            'files': [{'url': f'{RELEASE_URL}/v{version}/{native.name}',
                       'sha256': hashlib.sha256(native.read_bytes()).hexdigest()}],
        }, indent=2) + '\n', encoding='utf-8')
        artifacts.extend([native, framedrop_zip, manifest])
    checksums = output / 'SHA256SUMS'
    checksums.write_text(''.join(
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n'
        for path in artifacts
    ), encoding='ascii')
    return [*artifacts, checksums]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist', help='Destination directory (default: repository dist/)')
    parser.add_argument('--list', action='store_true', help='List allowlisted files without creating archives')
    parser.add_argument('--framedrop-launcher', type=Path, help='Linux ARM64 frame-hev-setup stub; also build the standalone installer, ZIP, and manifest')
    args = parser.parse_args()
    try:
        files = files_for_release()
        version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
        if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?', version):
            raise ValueError('VERSION must contain a version such as 0.1.0 or 0.1.0-rc.1')
        if args.list:
            for path in files:
                print(path.relative_to(ROOT).as_posix())
        else:
            for path in build(args.output_dir.resolve(), version, files, framedrop_launcher=args.framedrop_launcher):
                print(path)
    except (OSError, ValueError) as error:
        parser.exit(1, f'build_release: {error}\n')


if __name__ == '__main__':
    main()
