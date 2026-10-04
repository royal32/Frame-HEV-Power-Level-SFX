#!/usr/bin/env bash
# Run on the Frame as its logged-in Steam user.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_dir/install.py" "$@"
