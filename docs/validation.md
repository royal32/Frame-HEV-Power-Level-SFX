# Validation record

Tested on a Steam Frame on October 3, 2026.
SteamOS 0.4.3, variant `vr`, build `20260930.6234839`, aarch64 Linux 6.18.

- All 34 downloaded WAV files matched recorded SHA-256, size, frame count,
  and PCM format. The runtime uses 32 clips; fuzz and power-restored are available
  for future variations.
- All 21 unit tests ran on both the development Mac and Frame, covering exact phrase
  assembly at every integer from 0 through 100, PCM/cache integrity, battery
  selection, button timing, repeat/bounce handling, aux isolation, and wake-event
  rejection when suspend occurs inside `select()`.
- Three of those regression tests cover stale queued actions after
  suspend, sleep during the logind query, and the final check before a subprocess
  starts. These prevent a pre-suspend queued action from immediately sleeping the
  Frame again after wake.
- `tools/smoke_input.py` ran on the Frame with a virtual power button: double tap
  announced, single tap selected the short action, and hold selected the long
  action. A second input reader received no grabbed events and resumed receiving
  them after the process stopped. All power actions were dry-run for this test.
- `systemd-analyze --user verify` accepted the user service. The daemon reached
  readiness after grabbing `pmic_pwrkey` and the installer enabled startup under
  `graphical-session.target`.
- A manual current-battery announcement completed through `pw-play` at 80%.
  The running service also logged a physical double-tap announcement at 80%.
- **User confirmed: “Double tap speaks; sleep/wake works.”**
- Live logs confirmed the forwarded single press and wake-event guard after
  resume. The final resume-queue fix was deployed, all 21 tests and the virtual
  input smoke check passed again on the Frame, and deployed/source SHA-256 hashes
  matched. Both HEV and native power services were active afterward; HEV startup
  registration was enabled.
- The UI helper's live target listing, rendered DOM inspection, and PNG screenshot
  succeeded through an SSH tunnel. Eight local DOM-selection cases passed,
  including ambiguity, hidden/disabled controls, covered controls, and case
  sensitivity. Actual remote clicking was not exercised.

Not yet physically checked: long-hold menu with the custom daemon, aux fallback,
cold-boot startup, or behavior across a SteamOS update. Native single/long action
selection is tested; these remaining device checks should not be inferred from
the automated results. There is no change to boot firmware or the native power
button service.

## Public package validation

- The release contains source, installers, license, and documentation. Original
  WAV files, machine-specific agent instructions, settings, and Git history are
  excluded through an explicit release file list. ZIP and tar.gz builds are
  reproducible from identical source bytes and ship with SHA-256 checksums.
- The macOS/Linux root installer was exercised from the extracted release
  archive against the real Frame over SSH. It transferred an audio-free package,
  reused 34 verified clips from the existing installation, and successfully
  enabled the updated user service.
- The automated suite has 36 tests: 21 runtime tests, 10 downloader tests, and
  5 installer transaction tests with multiple failure/state combinations.
  Downloader tests cover four concurrent requests, offline/cache imports, and
  preservation of existing files after corrupt downloads. Installer tests cover
  preserved settings, failures before replacement, and rollback of app files
  and service state after startup failure.
- Bash syntax and one-connection transport were checked. Source-directory
  arguments containing quotes, command substitution syntax, and newlines were
  preserved literally. Windows uses a file transfer rather than a binary
  PowerShell pipeline; its remote argument-decoding protocol was checked, but
  the PowerShell script has not been executed on Windows.

## Installer troubleshooting changes (unreleased)

- All 44 Python tests pass on the development Mac, including eight new
  preflight tests for inherited desktop-session variables, absent session bus,
  service-manager errors, missing/masked native units, inactive/failed state,
  and incomplete status output. Existing staging and rollback tests still pass.
- Bash syntax and `git diff --check` pass. Neither the button runtime nor the
  systemd unit was changed; the native service requirement remains in place.
- Windows changes add an early ISE/redirected-input check, prompt guidance, and
  noninteractive cleanup after failed uploads. They have not been executed in
  Windows; the reporting user's terminal and exact cause remain unknown.
- A read-only SSH check could not resolve `frame.local` during this validation.
  No device installation or service changes were performed. The local-terminal
  report still needs its actual service status to confirm the cause.
