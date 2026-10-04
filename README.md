# Steam Frame HEV battery announcements

While the Frame is awake, double-tap its **power button** to hear its current battery percentage
in the original Half-Life HEV suit voice. A single tap waits briefly for a second
tap, then invokes Steam's normal sleep action. Holding power invokes Steam's
normal power menu.

This is a small Python standard-library application for the Frame's SteamOS
user session. It uses the existing PipeWire audio server and installs entirely
under the Steam user's home directory. No root access, package installation,
or SteamOS filesystem unlock is required on the tested Frame.

## Install on the Frame

Obtain the audio locally first. The WAV files are ignored by Git; their original
source URLs and SHA-256 checksums are recorded in `assets/manifest.json`.

```sh
python3 tools/fetch_sounds.py
python3 tools/fetch_sounds.py --check
```

Copy this directory to the Frame (for example `~/frame-hev-dev`), then run as
`steamos` in its running Steam session:

```sh
cd ~/frame-hev-dev
bash tools/install.sh
```

The installer verifies the audio and runtime requirements before enabling the
user service. The native `steamos-powerbuttond` must be running. Its service and
configuration are left intact.

## Use and configure

- **Double tap:** “Power level is … percent,” with the exact reported integer.
- **Single tap:** normal Steam sleep action after the double-tap window.
- **Hold:** normal Steam power menu at one second.
- **0%:** original “Armor compromised” clip; the original HEV vocabulary has no zero.

The default double-tap gap is 350 ms from the first release to the next press.
Announcement volume is 65% of the current headset output volume. Adjust
`~/.config/frame-hev/environment`, then restart the service:

```sh
systemctl --user restart frame-hev
systemctl --user status frame-hev
journalctl --user -u frame-hev -f
```

To announce immediately over SSH:

```sh
python3 ~/.local/share/frame-hev/frame_hev.py announce
```

The first second after waking ignores button events to avoid treating the wake
press as a request to sleep again. Wait a moment after waking, then double-tap.

`FRAME_HEV_BUTTON=aux` selects the auxiliary button above power as an optional
fallback. That mode observes `KEY_SELECT` without grabbing its shared input
device, so the existing auxiliary-button behavior also runs. Power mode is the
intended default.

## Stop or remove

```sh
systemctl --user stop frame-hev
# Or remove startup registration:
bash ~/frame-hev-dev/tools/uninstall.sh
```

The daemon exclusively reads the dedicated power-button input device while it
is running. Stopping it or a process exit releases that grab, allowing the
already-running native handler to receive subsequent presses again. The
service does not replace firmware behavior or change power-button boot logic.

The uninstall script retains the app, audio, and settings for easy reinstall.

## Development and tests

```sh
python3 -m unittest discover -s tests -v
python3 tools/fetch_sounds.py --check
python3 frame_hev.py doctor
# On the Frame, exercise the real evdev/uinput path without issuing Steam actions:
python3 tools/smoke_input.py
```

[Frame development notes](docs/frame-development.md) record the actual button,
battery, audio, SSH, and browser-debugging interfaces discovered on the headset.
The repository also includes a small remote UI inspection/click/screenshot
helper for future development.

[Validation record](docs/validation.md) distinguishes automated results,
physical confirmation, and checks that have not been performed.

See [audio provenance](assets/README.md) for original clips, import instructions,
and the distinction between original recordings and the assembled phrases.
