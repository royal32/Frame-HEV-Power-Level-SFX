#!/usr/bin/env bash
set -euo pipefail
purge=false
case "${1:-}" in
    --purge) purge=true; shift ;;
    --help|-h)
        echo 'Usage: frame-hev uninstall [--purge]'; exit 0 ;;
    '') ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
esac
if [[ $# -ne 0 ]]; then echo 'Unexpected arguments.' >&2; exit 2; fi
if [[ "$(id -u)" == 0 ]]; then echo 'Run without sudo, as the Steam user.' >&2; exit 1; fi
if systemctl --user cat frame-hev.service >/dev/null 2>&1; then
    # If stopping fails, keep the files so the running service can be diagnosed.
    systemctl --user stop frame-hev.service
    systemctl --user disable frame-hev.service
fi
rm -f -- "$HOME/.config/systemd/user/frame-hev.service" "$HOME/.local/bin/frame-hev"
systemctl --user daemon-reload
systemctl --user reset-failed frame-hev.service 2>/dev/null || true
rm -rf -- "$HOME/.local/share/frame-hev"
if $purge; then
    rm -rf -- "$HOME/.config/frame-hev" "$HOME/.cache/frame-hev"
fi
echo 'Frame HEV removed. Native power-button behavior is restored.'
if ! $purge; then echo 'Settings and cache retained; use --purge when uninstalling to remove them too.'; fi
