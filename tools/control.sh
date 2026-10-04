#!/usr/bin/env bash
# Frame HEV control: installed as ~/.local/bin/frame-hev.
set -euo pipefail
app_dir="$HOME/.local/share/frame-hev"
action="${1:-announce}"
if [[ $# -gt 0 ]]; then shift; fi
case "$action" in
    announce|doctor)
        if [[ -f "$HOME/.config/frame-hev/environment" ]]; then
            set -a
            source "$HOME/.config/frame-hev/environment"
            set +a
        fi
        exec python3 "$app_dir/frame_hev.py" "$action" "$@"
        ;;
    start|stop|restart|status)
        exec systemctl --user "$action" frame-hev.service "$@"
        ;;
    logs)
        exec journalctl --user -u frame-hev.service -f "$@"
        ;;
    uninstall)
        exec bash "$app_dir/uninstall.sh" "$@"
        ;;
    help|--help|-h)
        cat <<'USAGE'
Usage: ~/.local/bin/frame-hev [command]
  announce [--percent N]   Speak the current battery (or preview N percent)
  doctor                  Show battery, input, and audio diagnostics
  status                  Show service status
  logs                    Follow service logs (Ctrl+C to exit)
  start | stop | restart  Control the announcement service
  uninstall [--purge]     Remove the app; --purge also removes settings/cache

Settings: ~/.config/frame-hev/environment
After editing settings: ~/.local/bin/frame-hev restart
USAGE
        ;;
    *) echo "Unknown command: $action. Run frame-hev --help." >&2; exit 2 ;;
esac
