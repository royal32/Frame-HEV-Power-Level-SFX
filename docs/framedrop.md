# FrameDrop package

FrameDrop's [official install format](https://framedropvr.com/docs/) accepts an
HTTPS manifest with `schema: framedrop.install/v1`, a Steam title, and a payload
URL with a SHA-256 checksum. Its [pairing flow](https://framedropvr.com/how-to/) uses
the Frame's developer pairing dialog. This avoids the manual SSH password and
host-key prompts in our terminal installers.

The README button opens the published `frame-hev.framedrop.json` asset from the
latest GitHub release. That manifest points to a specific version's standalone
Linux ARM64 `.bin` installer and its checksum, generated from the actual bytes.
The ZIP asset remains available for manual drag-and-drop.

The README button uses this repository's `docs/install.html` on GitHub Pages.
It opens the manifest through `framedrop://` once. FrameDrop's public `/install`
page was observed sending the same request twice, via a hidden iframe and a
top-level navigation. After the v0.1.2 download fix, the user confirmed a
successful Windows installation followed by a second installation offer.
Using one navigation avoids submitting that duplicate request. The page includes
an explicit retry link and links to FrameDrop, manual downloads, and the guide.
GitHub Pages must publish `main:/docs`; `.nojekyll` keeps the HTML unchanged.

## FrameDrop 1.0.37 and GitHub redirects

The v0.1.1 README button failed in FrameDrop 1.0.37 with “isn't an APK, zip,
Windows exe, or Linux binary.” The same ZIP downloaded in a browser and dropped
into FrameDrop succeeded in the user's Windows test.

Inspection of the official 1.0.37 installer identified the cause. Its downloader
uses the last path component of `response.geturl()` as the saved filename and
ignores `Content-Disposition`. GitHub redirects release assets to
`release-assets.githubusercontent.com` URLs whose path ends with an identifier,
without a file extension. FrameDrop's `_is_zip_bundle` requires `.zip` as well
as valid ZIP contents, so the correctly downloaded ZIP is rejected. The public
download was checked with FrameDrop's user agent: its checksum matched, its
bytes were a valid ZIP, and its final URL basename had no extension.

HEV v0.1.2 uses a standalone ELF executable, which FrameDrop identifies from its
first four bytes. It contains the setup source and works after being renamed.
Switching from `manifest=` to a direct GitHub `url=` would still encounter the
same redirect and would not repair ZIP detection. For v0.1.1, use the browser
download followed by drag-and-drop.

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

The standalone installer starts with a native Linux ARM64 launcher and ends
with a ZIP containing `__main__.py` and the full source under `payload/`.
The launcher uses `/proc/self/exe` to locate itself and passes that path to the
Frame's `/usr/bin/python3`. Python runs the embedded ZIP entry point, extracts
it to a private temporary directory, and runs the graphical `tools/framedrop.py`
entry point. Extraction remains available until setup exits, then is removed.
The installed service uses its normal permanent application directory.

This works independently of the filename, working directory, or spaces in the
upload path. The drag-and-drop ZIP contains the same standalone installer as
its sole file, `frame-hev-setup`. Zenity supplies the windows; it is present on
the tested Frame. To inspect the embedded source without running setup:

```sh
python3 -m zipfile -e frame-hev-0.1.2-linux-arm64.bin extracted-source
```

On Linux ARM64 (including a Frame with its existing compiler):

```sh
cc -O2 -Wall -Wextra -Werror -o /tmp/frame-hev-setup tools/framedrop-launcher.c
```

Copy that binary to the build computer, then from this repository run:

```sh
python3 -m unittest discover -s tests -v
python3 tools/build_release.py --framedrop-launcher /path/to/frame-hev-setup
```

The builder rejects other architectures and generates six release assets:

- `frame-hev-<version>.zip`: terminal/source package.
- `frame-hev-<version>.tar.gz`: terminal/source package.
- `frame-hev-<version>-linux-arm64.bin`: standalone FrameDrop installer with source.
- `frame-hev-<version>-linux-arm64.zip`: the same installer, zipped for manual upload.
- `frame-hev.framedrop.json`: manifest naming the versioned `.bin` and its hash.
- `SHA256SUMS`: checksums for the other five files.

Publish all six on the matching `v<version>` GitHub release. The README's stable
button follows `releases/latest/download/frame-hev.framedrop.json`. It cannot
work until a release with that asset is published. Build after all source and
documentation edits; rebuilding the installer changes its hash and requires the
matching regenerated manifest. The source and native launcher use the repository's
GPL-3.0-only license.

## Validation scope

Automated checks cover checksum/URL consistency, reproducible archives, a single
native launch target, source inclusion, absence of WAV files, architecture
rejection, execution after an extensionless rename, temporary extraction and
cleanup, install/cancel/update/uninstall choices, session selection, progress
cleanup, and visible error logs. Actual device checks are recorded in
[validation.md](validation.md).

The manifest follows the published FrameDrop format. The user confirmed a
successful Windows FrameDrop 1.0.37 install using the manually downloaded
v0.1.1 ZIP. The v0.1.2 button also installed successfully on Windows, but the
user then saw the duplicate offer from FrameDrop's public launch page. The
replacement launch page still needs a Windows end-to-end check after deployment.
