#!/usr/bin/env bash
# Run from an extracted release or a source checkout on macOS, Linux, or Frame.
set -euo pipefail

usage() {
    cat <<'HELP'
Usage: bash install.sh [options]

Install HEV announcements on your Steam Frame. On a computer, the installer
uploads the required files and installs them over one SSH connection.

  --host USER@HOST  Frame SSH destination (default steamos@frame.local)
  --local           Run directly on this device
  --offline         Do not download audio; use verified Frame-local files
  --source-dir PATH Import audio from this directory on the Frame
  --help            Show this help

The Frame must be awake, connected to the same network, and accessible by SSH.
SSH handles your password or existing key. No password is stored by this script.
HELP
}

fail() { printf 'Install error: %s\n' "$*" >&2; exit 1; }
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
host='steamos@frame.local'
local_install=0
offline=0
audio_source=''
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host|--source-dir)
            [[ $# -ge 2 && -n "$2" ]] || fail "$1 needs a value."
            if [[ "$1" == --host ]]; then host="$2"; else audio_source="$2"; fi
            shift 2 ;;
        --local) local_install=1; shift ;;
        --offline) offline=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "Unknown option: $1. Use --help." ;;
    esac
done
[[ "$host" =~ ^([A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*$ ]] ||
    fail 'Use a hostname or IPv4 address, optionally preceded by USER@ (no SSH options or spaces).'

is_frame() {
    [[ -r /etc/os-release ]] || return 1
    local ID='' VARIANT_ID='' device_name='' input_name
    # Read the operating system's own identification file.
    . /etc/os-release
    [[ "$ID" == steamos && "$VARIANT_ID" == vr ]] || return 1
    for input_name in /sys/class/input/event*/device/name; do
        [[ -r "$input_name" ]] || continue
        IFS= read -r device_name < "$input_name" || continue
        [[ "$device_name" == pmic_pwrkey ]] && return 0
    done
    return 1
}

installer_args=()
[[ "$offline" == 0 ]] || installer_args+=(--offline)
[[ -z "$audio_source" ]] || installer_args+=(--source-dir "$audio_source")
if [[ "$local_install" == 1 ]] || is_frame; then
    exec bash "$repo_dir/tools/install.sh" ${installer_args[@]+"${installer_args[@]}"}
fi

for command in ssh tar; do
    command -v "$command" >/dev/null || fail "$command is required."
done
files=(
    VERSION README.md LICENSE frame_hev.py assets/README.md assets/manifest.json
    config/environment systemd/frame-hev.service tools/install.sh tools/install.py
    tools/uninstall.sh tools/control.sh tools/fetch_sounds.py
)
for file in "${files[@]}"; do
    [[ -f "$repo_dir/$file" && ! -L "$repo_dir/$file" ]] || fail "Missing release file: $file. Extract the whole download first."
done

# POSIX single-quote escaping: the remote login shell sees data, never commands
# contained in a source-directory argument.
shell_quote() {
    local quote="'" escaped="'\"'\"'" value
    value="${1//$quote/$escaped}"
    printf "'%s'" "$value"
}
remote_script="$(cat <<'REMOTE'
set -euo pipefail
offline=$1
audio_source=$2
work=$(mktemp -d "${TMPDIR:-/tmp}/frame-hev.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
# The shell program comes from -c, leaving stdin exclusively for tar.
tar -xf - -C "$work"
args=()
[[ "$offline" == 0 ]] || args+=(--offline)
[[ -z "$audio_source" ]] || args+=(--source-dir "$audio_source")
bash "$work/tools/install.sh" "${args[@]}" </dev/null
REMOTE
)"
remote_command="bash -c $(shell_quote "$remote_script") -- $(shell_quote "$offline") $(shell_quote "$audio_source")"
printf 'Installing on %s. SSH may ask for your Frame password.\n' "$host"
tar_args=(-cf - -C "$repo_dir")
if [[ "$(uname -s)" == Darwin ]]; then
    tar_args=(--no-xattrs "${tar_args[@]}")
fi
COPYFILE_DISABLE=1 tar "${tar_args[@]}" "${files[@]}" |
    ssh -T -- "$host" "$remote_command"
