# FrameDrop package

FrameDrop's [official install format](https://framedropvr.com/docs/) accepts an
HTTPS manifest with `schema: framedrop.install/v1`, a Steam title, and a ZIP URL
with a SHA-256 checksum. Its [pairing flow](https://framedropvr.com/how-to/) uses
the Frame's developer pairing dialog. This avoids the manual SSH password and
host-key prompts in our terminal installers.

The README button opens the published `frame-hev.framedrop.json` asset from the
latest GitHub release. That manifest points to a specific version's Linux
ARM64 ZIP and its checksum; it is generated from the actual ZIP bytes.

## Headset behavior

FrameDrop transfers the package and adds **Frame HEV** to the Steam library.
The user launches that entry once and confirms **Install**. The installer
downloads/verifies the original audio and enables the existing HEV user
service. A progress window remains visible while it runs. Success is shown in
a dialog; failures show their log. The native power-button handler remains
intact. Closing the setup app does not stop the installed service.

Subsequent launches offer **Install / Update** and **Uninstall**. Uninstall
removes HEV's service and application using the existing remover, preserving
settings and the audio cache. The user can then remove the Steam library entry
separately. Deleting only the library entry does not stop the background service.

The log is `~/.local/state/frame-hev/install.log` on the Frame. Each setup run
replaces it. An unattended command-line check can run:

```sh
./frame-hev-setup --no-ui
```

That performs a real install/update. `--help` only prints usage; normal launch
shows the graphical installer. No audio or Python interpreter is bundled.

## Build and release

The bundle has one executable at its root: `frame-hev-setup`, a native Linux
ARM64 launcher. Everything else is in `payload/` with non-executable archive
modes. The launcher finds its payload relative to `/proc/self/exe`, so Steam's
working directory and spaces in the upload path do not matter. It starts the
Frame's `/usr/bin/python3` and the graphical `tools/framedrop.py` entry point.
Zenity supplies the windows; it is present on the tested Frame.

On Linux ARM64 (including a Frame with its existing compiler):

```sh
cc -O2 -Wall -Wextra -Werror -o /tmp/frame-hev-setup tools/framedrop-launcher.c
```

Copy that binary to the build computer, then from this repository run:

```sh
python3 -m unittest discover -s tests -v
python3 tools/build_release.py --framedrop-launcher /path/to/frame-hev-setup
```

The builder rejects other architectures and generates five release assets:

- `frame-hev-<version>.zip`: terminal/source package.
- `frame-hev-<version>.tar.gz`: terminal/source package.
- `frame-hev-<version>-linux-arm64.zip`: FrameDrop package, including source.
- `frame-hev.framedrop.json`: manifest naming the versioned ZIP and its hash.
- `SHA256SUMS`: checksums for the other four files.

Publish all five on the matching `v<version>` GitHub release. The README's stable
button follows `releases/latest/download/frame-hev.framedrop.json`. It cannot
work until a release with that asset is published. Build after all source and
documentation edits; rebuilding the ZIP changes its hash and requires the
matching regenerated manifest. The source and native launcher use the repository's
GPL-3.0-only license.

## Validation scope

Automated checks cover checksum/URL consistency, reproducible archives, a single
native launch target, source inclusion, absence of WAV files, architecture
rejection, install/cancel/update/uninstall choices, session selection, progress
cleanup, and visible error logs. Actual device checks are recorded in
[validation.md](validation.md).

The manifest and bundle follow the published FrameDrop format and the
[Passthrough Shortcuts example](https://github.com/KominoVR/frame-passthrough-shortcuts).
Windows FrameDrop transfer and its automatic launch-target selection still need
an end-to-end check in FrameDrop. Local package and headset tests alone do not
verify the Windows client.
