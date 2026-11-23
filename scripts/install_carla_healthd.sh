#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target="${HEALTHD_INSTALL_TARGET:-$HOME/bin}"
backup_root="$HOME/.local/share/roaring/carla-healthd-backups"
stamp="$(date +%Y%m%d%H%M%S)-$$"
backup_dir="$backup_root/$stamp"
files=(
  roaring_carla_healthd.py
  audio_health_baseline.py
  audio_health_signal.py
  audio_health_stream.py
  audio_health_transport.py
)

mkdir -p "$target"
stage="$(mktemp -d "$target/.carla-healthd-stage.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT

for file in "${files[@]}"; do
  source_file="$repo_root/scripts/$file"
  if [[ ! -f "$source_file" ]]; then
    echo "Missing runtime source: $source_file" >&2
    exit 1
  fi
  cp -- "$source_file" "$stage/$file"
  if [[ "$file" == "roaring_carla_healthd.py" ]]; then
    chmod 0755 "$stage/$file"
  else
    chmod 0644 "$stage/$file"
  fi
done

python3 -m py_compile "$stage"/*.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$stage" python3 -c "import roaring_carla_healthd, audio_health_baseline, audio_health_signal, audio_health_stream, audio_health_transport"

has_existing=false
for file in "${files[@]}"; do
  if [[ -f "$target/$file" ]]; then
    has_existing=true
    break
  fi
done
if [[ "$has_existing" == "true" ]]; then
  mkdir -p "$backup_dir"
  for file in "${files[@]}"; do
    if [[ -f "$target/$file" ]]; then
      cp -a -- "$target/$file" "$backup_dir/$file"
    fi
  done
fi

for file in "${files[@]}"; do
  mv -f -- "$stage/$file" "$target/$file"
done

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$target" python3 -c "import roaring_carla_healthd, audio_health_baseline, audio_health_signal, audio_health_stream, audio_health_transport"

echo "Installed Carla health monitor and all runtime helpers to $target"
if [[ -d "$backup_dir" ]]; then
  echo "Backups: $backup_dir"
fi
echo "No systemd unit changed or restarted. Restart the healthd service manually when ready."
