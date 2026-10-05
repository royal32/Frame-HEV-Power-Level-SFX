# Steam Frame development notes

These observations were collected from a Steam Frame running SteamOS `0.4.3`, variant `vr`, build `20260930.6234839`, with an aarch64 Linux `6.18` kernel. Recheck paths and capabilities after firmware updates.

## Remote Steam UI

Steam exposes Chromium DevTools Protocol (CDP) on the Frame at `http://127.0.0.1:8080`. Its `/json/list` endpoint was verified and includes the main `SteamVR` page, Steam overlays, a keyboard, notifications, and a `SharedJSContext` target. Select a rendered page such as `SteamVR` for UI work; the shared JavaScript context may have no useful visible UI.

The visible Developer settings page includes a **CEF Remote Debugging** toggle described as auto-enabling debugging at startup. Its presence was verified without changing it. If the endpoint disappears after a restart, check that setting on the device before trying the tunnel again.

Open an SSH tunnel from the development Mac, leaving this command running:

```sh
ssh -N -L 9222:127.0.0.1:8080 steamos@frame.local
```

The repository includes a dependency-free Node.js 22+ helper. First discover the current targets, then copy the desired page ID into subsequent commands:

```sh
node tools/frame-ui.mjs list
node tools/frame-ui.mjs inspect --target TARGET_ID
node tools/frame-ui.mjs screenshot --target TARGET_ID --output /tmp/frame-ui.png
node tools/frame-ui.mjs click --target TARGET_ID --label 'Exact visible label'
```

`inspect` reports text and controls rendered in the page viewport. `click` requires exactly one visible control with the specified label, rejects disabled or covered controls, and sends mouse input through CDP. Labels match after whitespace normalization; case remains significant. Icon-only controls and custom controls without a recognized HTML tag or role may require a different tool. Inventory and clicks use DOM text and layout, without reading application stores or framework internals. Screenshots contain the selected Chromium page, not the complete headset view.

The helper requires explicit target selection for every page command and refreshes `/json/list` each time. IDs change when Steam restarts. Use `--endpoint http://127.0.0.1:PORT` for a different tunnel and `--timeout 20000` for a slower connection. `--help` lists the command syntax. A connection error usually means the tunnel is absent, its port is already occupied, or the selected target has closed.

Only `list`, `inspect`, and `screenshot` are read-only operations. `click` changes the UI and should follow a fresh inventory or screenshot. Keep debugging access behind the SSH tunnel; CDP gives control over the Steam page.

## Power-button integration

The native Steam ARM64 executable is `~/.steam/steam/steamrtarm64/steam`. Strings and disassembly confirm native handling for these URIs:

```sh
~/.steam/steam/steamrtarm64/steam -ifrunning steam://shortpowerpress
~/.steam/steam/steamrtarm64/steam -ifrunning steam://longpowerpress
```

These commands deliver native power-button behavior to the running Steam process and are useful when forwarding physical button input. They are UI actions, not read-only diagnostic probes.

The physical power device was named `pmic_pwrkey` and emitted `KEY_POWER` (`116`). Other discovered devices were `pmic_resin` with volume-down (`114`) and `gpio-keys` with volume-up (`115`) and an auxiliary `KEY_SELECT` (`353`). Device numbers such as `event0` change across boots; discover them through `/sys/class/input/event*/device/name` rather than hardcoding an event path.

The `steamos` account had UID `1000` and membership in the `input` group. `/dev/uinput` was writable by that group (`crw-rw-r--`, group `input`). The native button daemon can remain running while an application takes `EVIOCGRAB` on the power device only. Grabbing unrelated input devices would also suppress their native handling.

Battery percentage was available at `/sys/class/power_supply/max1720x_bat_7-36/capacity`. `pw-play` and `wpctl` were installed for PipeWire playback and audio inspection. These paths and permissions are device observations, not a promise about future firmware.

## Other discovered interfaces

The system service configuration forwards external port `8081` to Steam's CDP port `8080`, and the VR endpoint from `8088` to `8087`. The SSH tunnel above uses the verified loopback endpoint and avoids relying on external forwarding.

The development-kit service was present at `/usr/share/steamos-devkit/steamos-devkit-service.py`, using port `32000`. An `adbd` service was running, and xrdp was listening on port `3389`. Their presence was confirmed; ADB pairing, authentication, transfer, and remote-desktop usability were not tested. Do not assume they provide a working connection solely because a service exists.

No SSH configuration changes or package installation are needed for the CDP helper. Credentials belong in the user's existing SSH setup, never in this repository or helper.

## Installer session diagnostics

A user reported that the v0.1.0 installer said to wake an already-awake Frame
when launched from a terminal on the headset. Code inspection confirmed that
the installer discarded the `systemctl --user is-active` diagnostic and used
that message for every nonzero result, including a service-manager connection
failure. It also preserved any inherited `XDG_RUNTIME_DIR` and
`DBUS_SESSION_BUS_ADDRESS`, which can differ in a desktop session. The user's
actual environment and service state have not yet been captured; this report
alone does not establish which failure occurred.

The installer now requires the current user's `/run/user/<uid>/bus` socket and
sets both session variables explicitly before its subprocesses run. It queries
`LoadState`, `ActiveState`, `SubState`, and `Result` to distinguish a missing
native unit, an inactive/failed unit, and a query failure. These checks only
read native service state. Never bypass them or restart/disable the native
handler to make installation proceed.

For v0.1.0 troubleshooting, the README includes an `env ... bash install.sh
--local` retry and a read-only service-status command using the same session.
The environment selection and error cases have automated regression coverage;
reproduction in the affected user's desktop session remains unverified.

The same report described a Windows host-key prompt that accepted no keyboard
input. The user's terminal host is unknown. Microsoft's
[PowerShell documentation](https://devblogs.microsoft.com/powershell/console-application-non-support-in-the-ise/)
confirms that ISE cannot run interactive console applications. The installer
now rejects ISE and redirected stdin before attempting SSH, and failed-upload
cleanup uses `BatchMode=yes` with a connection timeout so cleanup cannot open
another password/host-key prompt. Standard SSH host-key checking is preserved.
Real Windows prompt behavior still needs a Windows test.
