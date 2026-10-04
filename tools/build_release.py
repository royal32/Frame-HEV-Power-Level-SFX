#!/usr/bin/env python3
"""Build reproducible, audio-free release archives with Python's standard library."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import re
import stat
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = (
    'VERSION', 'README.md', 'LICENSE', 'install.sh', 'install.ps1', 'frame_hev.py',
    'assets/README.md', 'assets/manifest.json', 'config/environment',
    'systemd/frame-hev.service', 'tools/install.sh', 'tools/install.py', 'tools/uninstall.sh',
    'tools/control.sh', 'tools/fetch_sounds.py', 'tools/build_release.py',
)
OPTIONAL = (
    'LICENSE.md', 'docs/frame-development.md', 'docs/validation.md',
    'tools/frame-ui.mjs', 'tools/smoke_input.py', 'tests/test_frame_hev.py',
    'tests/test_fetch_sounds.py', 'tests/test_install.py',
)
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


def build(output: Path, version: str, files: list[Path]) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    prefix = f'frame-hev-{version}'
    zip_path = output / f'{prefix}.zip'
    tar_path = output / f'{prefix}.tar.gz'
    contents = [(path, path.read_bytes()) for path in files]
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, data in contents:
            info = zipfile.ZipInfo(f'{prefix}/{path.relative_to(ROOT).as_posix()}', (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | file_mode(path)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    with tar_path.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=EPOCH, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
                for path, data in contents:
                    info = tarfile.TarInfo(f'{prefix}/{path.relative_to(ROOT).as_posix()}')
                    info.size = len(data)
                    info.mode = file_mode(path)
                    info.mtime = EPOCH
                    archive.addfile(info, io.BytesIO(data))
    checksums = output / 'SHA256SUMS'
    checksums.write_text(''.join(
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n'
        for path in (zip_path, tar_path)
    ), encoding='ascii')
    return [zip_path, tar_path, checksums]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist', help='Destination directory (default: repository dist/)')
    parser.add_argument('--list', action='store_true', help='List allowlisted files without creating archives')
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
            for path in build(args.output_dir.resolve(), version, files):
                print(path)
    except (OSError, ValueError) as error:
        parser.exit(1, f'build_release: {error}\n')


if __name__ == '__main__':
    main()
