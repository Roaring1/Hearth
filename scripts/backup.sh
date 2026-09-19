#!/usr/bin/env bash
# Snapshot the working tree and the full git history.
#
# This exists because the snapshot was being taken by hand, and the hand-typed
# exclude list drifted behind .gitignore: one archive came out at 1.6 MB
# because .mypy_cache was not excluded. The exclusions below are derived from
# git itself rather than retyped, so they cannot drift again.
#
# Nothing in this project is committed yet, so these archives are the only
# safety net that exists. Two of them, deliberately:
#   * a tarball, restorable without git;
#   * a bundle of every ref, restorable without GitHub.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${HEARTH_BACKUP_DIR:-$HOME/Backups/hearth}"
STAMP="$(date +%Y%m%d-%H%M%S)"
NAME="$(basename "$REPO")"

mkdir -p "$DEST"
cd "$REPO"

# Everything git ignores, plus git's own directory. Read from the repository
# so a new cache directory added to .gitignore is excluded automatically.
EXCLUDES=(--exclude-vcs)
while IFS= read -r path; do
  [[ -n "$path" ]] && EXCLUDES+=(--exclude="${path#./}")
done < <(git ls-files --others --ignored --exclude-standard --directory | sed 's:/*$::')

TARBALL="$DEST/${NAME,,}-worktree-$STAMP.tar.gz"
BUNDLE="$DEST/${NAME,,}-git-$STAMP.bundle"

tar "${EXCLUDES[@]}" -czf "$TARBALL" -C "$(dirname "$REPO")" "$NAME"
git bundle create "$BUNDLE" --all >/dev/null 2>&1

# A backup nobody verified is a rumour, so both are checked here.
ENTRIES="$(tar -tzf "$TARBALL" | wc -l)"
SOURCES="$(tar -tzf "$TARBALL" | grep -c 'src/hearth' || true)"
git bundle verify "$BUNDLE" >/dev/null 2>&1 || {
  printf 'bundle verification FAILED: %s\n' "$BUNDLE" >&2
  exit 1
}

printf 'tarball  %s\n' "$TARBALL"
printf '         %s bytes, %s entries, %s under src/hearth\n' \
  "$(stat -c%s "$TARBALL")" "$ENTRIES" "$SOURCES"
printf 'bundle   %s (verified, %s bytes)\n' "$BUNDLE" "$(stat -c%s "$BUNDLE")"

# Scratch that would otherwise die with /tmp on reboot.
if compgen -G "/tmp/*.bak_step*" >/dev/null || compgen -G "/tmp/step*.py" >/dev/null; then
  SCRATCH="$DEST/scratch-$STAMP"
  mkdir -p "$SCRATCH"
  cp -a /tmp/*.bak_* /tmp/step*.py /tmp/step*.sh "$SCRATCH" 2>/dev/null || true
  printf 'scratch  %s (%s files)\n' "$SCRATCH" "$(find "$SCRATCH" -type f | wc -l)"
fi
