#!/usr/bin/env bash
# Install the Plasma-derived Hearth stylesheet.
#
# Finds the repo, verifies, backs up, installs, and rolls back if the
# result does not parse. Safe to run twice.
#
#   ./install_plasma_theme.sh              install
#   ./install_plasma_theme.sh --dry-run    verify only, write nothing
#   ./install_plasma_theme.sh --uninstall  put the original back

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Look for the repo in the usual place, then relative to this script, so the
# snapshot works whether it is unpacked next to the checkout or anywhere else.
for candidate in \
    "${HEARTH_REPO:-}" \
    "$here/.." \
    "$HOME/Documents/GitHub/Hearth" \
    "$here/../.."; do
    if [ -n "$candidate" ] && [ -f "$candidate/src/hearth/ui/style/tokens.css" ]; then
        repo="$(cd "$candidate" && pwd)"
        break
    fi
done

if [ -z "${repo:-}" ]; then
    echo "Could not find the Hearth checkout." >&2
    echo "Set HEARTH_REPO=/path/to/Hearth and run this again." >&2
    exit 2
fi

if [ -f "$repo/scripts/install_plasma_theme.py" ]; then
    installer="$repo/scripts/install_plasma_theme.py"
else
    installer="$here/install.py"
fi

# Run from outside the repo: a hearth.py shim at the repo root shadows the
# real package when the current directory is on sys.path.
cd /tmp
exec python3 "$installer" --repo "$repo" "$@"
