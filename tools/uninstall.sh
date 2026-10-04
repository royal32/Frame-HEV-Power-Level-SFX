#!/usr/bin/env bash
# Release the grab first. The untouched native daemon resumes receiving presses.
set -euo pipefail
systemctl --user disable --now frame-hev.service 2>/dev/null || true
rm -f -- "$HOME/.config/systemd/user/frame-hev.service"
systemctl --user daemon-reload
systemctl --user reset-failed frame-hev.service 2>/dev/null || true
echo 'HEV service removed; native power-button behavior restored.'
echo 'Audio, application, and settings retained under ~/.local/share/frame-hev and ~/.config/frame-hev.'
