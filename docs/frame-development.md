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

## FrameDrop launch checks

On October 5, 2026, this Frame had `cc`, `zenity` 4.0.1, `kdialog`, and `konsole`
available without installing packages. A small native AArch64 executable can
locate its package through `/proc/self/exe` and run `/usr/bin/python3` with a
script path relative to the executable. This worked from an unrelated working
directory, including a real update through the existing HEV installer.

The graphical entry point uses Zenity on Steam's display. For an SSH-launched
test, `systemctl --user show-environment` reported `DISPLAY=:0`; setting that
for the launcher opened the window, and the user confirmed successful setup.
Normal Steam launches supply the display themselves. Merely seeing an X11
window in `xwininfo` does not prove that it is visible or usable in the headset;
the user's confirmation supplied that check here.

The v0.1.1 FrameDrop ZIP has a single native executable at its root and helper
sources below `payload/`. The user subsequently confirmed successful Windows
FrameDrop 1.0.37 selection and transfer by downloading that ZIP in the browser
and dragging it into FrameDrop. The README button failed before transfer.

Static inspection of the official FrameDrop 1.0.37 download confirmed that
`fetch.download_file` derives its saved filename from the final redirect URL,
while `detect._is_zip_bundle` requires a `.zip` suffix. The public GitHub release
download redirects to an extensionless identifier, despite a correct
`Content-Disposition` filename and matching ZIP checksum. This explains the
button failure and why a direct `url=` link to the same ZIP would also fail.

The v0.1.2 package uses an ELF launcher with an appended Python ZIP application.
FrameDrop recognizes ELF magic even with an extensionless filename. Setup
extracts its source into a temporary directory until the installer exits; the
service still uses the permanent installation directory. See
[framedrop.md](framedrop.md) for the build procedure and
[validation.md](validation.md) for completed checks and remaining limits.

The user confirmed that the published v0.1.2 native download installed in
Windows FrameDrop 1.0.37, followed by a second installation offer. The public
FrameDrop launch page sent the same protocol request through both a hidden
iframe and top-level navigation. A script harness recorded two requests.
The README now uses `docs/install.html` on this repository's GitHub Pages site,
which sends one automatic request. After deployment the user confirmed that
only one installation offer appeared. Manual retries require an explicit click.

## Battery percentage sources

On October 5, 2026, the user reported Steam showing 51% then 50% while HEV spoke
46% then 45%. A read-only comparison later in the same session found Steam's
visible menu at **45%**, OpenVR's HMD `Prop_DeviceBatteryPercentage_Float` at
`0.449999988`, and the kernel's battery `capacity` at **41%**. UPower also used
the raw kernel value. Changing to UPower would therefore not resolve this
discrepancy. The difference is not a constant offset to add to the kernel value.

HEV now queries OpenVR's headset battery value for each announcement. The
standard-library `ctypes` binding uses the installed
`/opt/steamvr/bin/linuxarm64/libopenvr_api.so` and explicitly requests
`FnTable:IVRSystem_026`, matching Valve's pinned
[C API declaration](https://github.com/ValveSoftware/openvr/blob/0924064316de3effbcd1acf1e309182a2deb1c05/headers/openvr_capi.h).
Function-table slot 23 is `GetFloatTrackedDeviceProperty`; property 1012 on
device 0 is the headset's battery fraction. The fractional value must be
rounded, not truncated: `0.449999988` means 45%, not 44%.

This native query runs in a separate Python process with a two-second timeout
so a native-library crash or hang cannot terminate the process holding the
power input device. It connects as a background app and shuts down its own
connection after the read. Unsupported interfaces, invalid properties, missing
libraries, crashes, and timeouts fall back to the existing kernel battery
selection with a warning. `doctor` reports both values for diagnosis. No
percentage is cached and no hand-tuned correction is applied.

The first GUI progress window was shorter than the action/success dialogs,
and the user observed enlarged, overflowing text in the headset. The setup
windows now request the same 720×420 dimensions, with short, explicitly broken
lines in the progress message. Headset presentation must be checked in VR;
desktop window sizing alone cannot establish the apparent text size.
