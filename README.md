# Steam Frame HEV battery announcements

Double-tap your Steam Frame's power button to hear **“Power level is … percent”**
in the original Half-Life HEV suit voice.

- **Double tap while awake:** reads the exact battery percentage.
- **Single press:** puts the Frame to sleep, with a short 350 ms delay.
- **Hold:** opens Steam's normal power menu.

Runs in the background and starts with your Steam session. No root access,
additional packages, or SteamOS filesystem unlock needed.

## Install

### 1. Prepare the Frame once

On the headset, enable **Settings → System → Enable Developer Mode**, then open
**Settings → Developer → Set User Password** and choose a password. This enables
SSH access as `steamos`; use that password when the installer asks.
See [Valve's setup guide](https://partner.steamgames.com/doc/steamhardware/steamframe/setup)
and [SSH documentation](https://partner.steamgames.com/doc/steamhardware/steamframe/debugging).

Keep the Frame awake, with Steam running, and connect it to the same network as
your computer.

### 2. Download and run

Download the release ZIP, or choose **Code → Download ZIP** on GitHub, and
**extract the whole folder**. Open a terminal in that extracted folder.

**macOS or Linux:**

```sh
bash install.sh
```

**Windows PowerShell:**

Use a PowerShell tab in **Windows Terminal**, or press **Win+R**, type
`powershell`, and press Enter to open a normal console. PowerShell ISE cannot
handle SSH's interactive keyboard prompts. Run the command from the extracted
folder without piping or redirecting its input.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

Enter the Frame's password when SSH asks. Password characters do not appear as
you type. Windows may ask twice, once to upload and once to install. On the
first connection, SSH may ask you to accept the headset's host key.

The installer copies the app to the Frame, downloads and verifies the original
audio, and enables the background service. Your computer needs SSH and tar;
Windows also needs scp (part of OpenSSH Client). Python is only required on the
Frame, where SteamOS already provides it.

When it says **installed**, double-tap power on the awake headset. Installation
does not automatically play a sound.

If `frame.local` cannot be found, try `frame` or the Frame's IP address:

```sh
bash install.sh --host steamos@frame
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -HostName steamos@frame
```

**Already using a terminal on the Frame?** Extract the download there and run
`bash install.sh --local`. All installer commands run as the normal Steam user,
without `sudo`.

## Update or uninstall

To update, download a newer release and run the same installer again. It reuses
verified audio and preserves your settings. It prepares and checks the update
before replacing the running installation, and rolls back if startup fails.

To uninstall, connect with `ssh steamos@frame.local` and run:

```sh
~/.local/bin/frame-hev uninstall --purge
```

This removes the app, startup service, settings, and cached audio. Omit
`--purge` to keep your settings and cache. The native power-button handler
receives presses again when the HEV service stops.

## Controls and settings

These commands run **on the Frame**, either in its terminal or over SSH:

```sh
~/.local/bin/frame-hev announce   # Speak the current battery
~/.local/bin/frame-hev status     # Check the service
~/.local/bin/frame-hev logs       # Follow logs; Ctrl+C to exit
~/.local/bin/frame-hev stop       # Temporarily restore normal power behavior
~/.local/bin/frame-hev start
```

Edit `~/.config/frame-hev/environment`, then run
`~/.local/bin/frame-hev restart`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `FRAME_HEV_VOLUME` | `0.65` | Announcement volume from `0.0` to `1.0`, relative to headset volume |
| `FRAME_HEV_DOUBLE_TAP_MS` | `350` | Maximum gap between the first release and second press |
| `FRAME_HEV_BUTTON` | `power` | Use `aux` for the button above power instead |

Aux mode also allows the button's normal action to occur. Power mode is the
tested default. After waking the Frame, wait one second before double-tapping;
the wake press is ignored so it does not immediately put the headset to sleep.

At 0%, the original “Armor compromised” clip plays, because the HEV vocabulary
does not contain a recorded zero.

## Audio

The source repository and release downloads contain **no Half-Life audio**.
The installer fetches the original WAV clips from [hl1sfx.com](https://hl1sfx.com/)
and verifies their recorded SHA-256 hashes and WAV format. Audio remains Valve's
property and is not covered by the code's license.

To use loose audio files from your own Half-Life installation, point to its
`valve`, `sound`, or `fvox` directory **on the Frame**:

```sh
bash install.sh --source-dir /path/on/frame/to/Half-Life/valve
```

On Windows, use `-SourceDir` instead. `--offline` (Windows: `-Offline`) prevents
downloads and uses verified files already on the Frame. See
[audio provenance and import details](assets/README.md).

## Troubleshooting

- **Cannot connect:** keep the headset awake, check Developer Mode and your
  password, and try `ssh steamos@frame.local`. Try `frame` or the headset's IP if
  your network does not resolve `.local` names.
- **Windows prompt accepts no typing:** use Windows Terminal or a normal
  PowerShell console, rather than PowerShell ISE. Run `ssh steamos@frame.local`
  there first. At the host-key confirmation, type `yes` and press Enter after
  checking it is your Frame; at the password prompt, characters are invisible.
  After connecting, type `exit`, then rerun the installer from the extracted
  folder. If even standalone SSH cannot accept input, the issue is with the
  terminal/SSH client before the Frame installer starts.
- **“Wake the Frame” while it is already awake (v0.1.0):** that release uses
  the same error for an inactive native button service and a failed connection
  to the Steam user's service manager. It does not prove the headset is asleep.
  A desktop terminal can also have different session environment variables.
  From the extracted folder **on the Frame**, retry with the Steam user's session:

  ```sh
  env XDG_RUNTIME_DIR="/run/user/$(id -u)" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus" bash install.sh --local
  ```

  If it still fails, leave Steam/SteamVR running and collect the actual state
  with this read-only command on the Frame:

  ```sh
  env XDG_RUNTIME_DIR="/run/user/$(id -u)" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus" systemctl --user --no-pager --full status steamos-powerbuttond.service steamvr.service
  ```

  Include that output and your SteamOS version in a bug report. The current
  installer selects this session automatically and reports the specific
  failure. Keep the native power-button service installed and enabled.
- **No sound:** check headset volume, run `~/.local/bin/frame-hev announce`, then
  `~/.local/bin/frame-hev doctor` and `~/.local/bin/frame-hev logs`.
- **Download or checksum error:** retry. A failed download does not replace a
  working installation. If the source remains unavailable, use a local game
  copy as described above.
- **After a SteamOS update:** rerun the installer. Input or Steam interfaces may
  change across firmware versions.

## Development and releases

Tested on SteamOS 0.4.3, variant `vr`, build `20260930.6234839`. Physical double-tap
announcements and single-press sleep/wake were confirmed. The Windows installer
is provided, but has not yet been run end-to-end on Windows.

```sh
python3 -m unittest discover -s tests -v
python3 tools/build_release.py
```

The release builder creates `dist/frame-hev-0.1.0.zip`,
`dist/frame-hev-0.1.0.tar.gz`, and `dist/SHA256SUMS`, using the version in
`VERSION`. Attach those three files to a GitHub release. The builder uses an
explicit file list and excludes audio, credentials, local agent settings, and
Git history.

[Frame development notes](docs/frame-development.md) include remote UI
inspection, screenshots, and click automation.
[Validation details](docs/validation.md) separate automated results from
physical checks.

## License

Application code: **GPL-3.0-only**. See [LICENSE](LICENSE).
Half-Life audio is separate Valve material. This is an unofficial community
project and is not affiliated with or endorsed by Valve.
