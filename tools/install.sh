#!/usr/bin/env bash
# Run on the Frame as its logged-in Steam user. No root or OS unlock needed.
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
install_dir="$HOME/.local/share/frame-hev"
unit_dir="$HOME/.config/systemd/user"
config_dir="$HOME/.config/frame-hev"

if [[ "$(id -u)" == 0 ]]; then
    echo 'Run as the Steam user, not root.' >&2
    exit 1
fi

python3 "$source_dir/tools/fetch_sounds.py" --check
python3 "$source_dir/frame_hev.py" doctor
systemctl --user is-active --quiet steamos-powerbuttond.service || {
    echo 'Native steamos-powerbuttond must be running to provide fail-open button handling.' >&2
    exit 1
}
mkdir -p "$install_dir/assets/fvox" "$unit_dir" "$config_dir"
systemctl --user stop frame-hev.service 2>/dev/null || true
install -m 644 "$source_dir/frame_hev.py" "$install_dir/frame_hev.py"
install -m 644 "$source_dir/assets/manifest.json" "$install_dir/assets/manifest.json"
install -m 644 "$source_dir"/assets/fvox/*.wav "$install_dir/assets/fvox/"
install -m 644 "$source_dir/systemd/frame-hev.service" "$unit_dir/frame-hev.service"
if [[ ! -f "$config_dir/environment" ]]; then
    install -m 600 "$source_dir/config/environment" "$config_dir/environment"
fi
systemctl --user daemon-reload
systemctl --user enable --now frame-hev.service
sleep 1
systemctl --user is-active --quiet frame-hev.service
journalctl --user -u frame-hev.service -n 12 --no-pager
echo 'Installed. Double-tap power to announce; single press sleeps after a brief delay.'
