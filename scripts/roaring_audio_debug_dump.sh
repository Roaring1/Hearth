#!/usr/bin/env bash
# The write_cmd bodies below are single-quoted on purpose: they are handed to
# an inner `bash -lc`, which is what should expand them, not this shell.
# shellcheck disable=SC2016
set -euo pipefail

# 10/1/2026-2
# Summary:
# - Creates a timestamped debug bundle (tar.gz) on your Desktop.
# - Produces labeled, readable text dumps + a single all_errors.txt.
# - Safe to run anytime.

TS="$(date +%Y%m%d_%H%M%S)"
OUTDIR="$HOME/Desktop/roaring_debug_$TS"
TAR="$HOME/Desktop/roaring_debug_$TS.tar.gz"
CONF="$HOME/.config/hearth/roaring_mixer.conf"

mkdir -p "$OUTDIR"

log() { echo "[debug-dump] $(date +%H:%M:%S) $*"; }

# Best-effort: avoid low per-shell FD limits when querying PipeWire.
ulimit -n 1048576 2>/dev/null || true

ROARING_UNITS=(
  roaring-vm-sinks.service
  roaring-mic-busses.service
  roaring-mic-routesd.service
  roaring-audio-routesd.service
  lpd8-mixer.service
  roaring-carla-session.service
  roaring-vesktop-mic-watchd.service
  roaring-laptop-audio.service
)

PIPEWIRE_UNITS=(
  pipewire.service
  pipewire-pulse.service
  wireplumber.service
)

KEY_UNITS=( "${PIPEWIRE_UNITS[@]}" "${ROARING_UNITS[@]}" )

write_cmd() {
  local name="$1"; shift
  {
    echo "### CMD: $*"
    echo "### WHEN: $(date --iso-8601=seconds)"
    echo
    "$@" || true
  } > "$OUTDIR/$name"
}

write_raw_cmd() {
  local name="$1"; shift
  "$@" > "$OUTDIR/$name" 2>/dev/null || true
}

write_file_if_exists() {
  local path="$1"
  if [[ -f "$path" ]]; then
    mkdir -p "$OUTDIR/files$(dirname "$path")"
    cp -a "$path" "$OUTDIR/files$path" || true
  fi
}

log "writing summary..."
{
  echo "### roaring audio debug summary"
  echo "timestamp=$TS"
  echo "date=$(date --iso-8601=seconds)"
  echo "host=$(hostname)"
  echo "user=$USER"
  echo "kernel=$(uname -r)"
  echo "uptime=$(uptime -p 2>/dev/null || true)"
  echo
  echo "### config (roaring_mixer.conf)"
  if [[ -f "$CONF" ]]; then
    sed 's/^/  /' "$CONF"
  else
    echo "  (missing)"
  fi
  echo
  echo "### unit state"
  for u in "${KEY_UNITS[@]}"; do
    status="$(systemctl --user is-active "$u" 2>/dev/null || true)"
    [[ -z "$status" ]] && status="unknown"
    printf '%-40s %s\n' "$u" "$status"
  done
  echo
  echo "### limits"
  echo "ulimit_nofile=$(ulimit -n 2>/dev/null || true)"
  systemctl --user show pipewire-pulse.service -p LimitNOFILE 2>/dev/null || true
  echo
  echo "### unit files (carla)"
  systemctl --user list-unit-files 2>/dev/null | grep -i carla || true
  echo
  echo "### carla quick check"
  systemctl --user --no-pager --full status roaring-carla-session.service 2>&1 || true
  echo
  pgrep -fa carla 2>&1 || true
  echo
  echo "### failed units"
  systemctl --user --no-pager --failed 2>&1 || true
} > "$OUTDIR/00_summary.txt"

log "writing index..."
cat > "$OUTDIR/index.txt" <<'EOF'
00_summary.txt          - host/config snapshot + unit state overview
01_systemd_status.txt   - full systemctl status + filtered list-units
02_systemd_show.txt     - key fields (restarts/status/result) per unit
03_pactl_summary.txt    - pactl info + short sinks/sources/modules
04_pactl_full.txt       - full sinks + sources
05_wpctl.txt            - wpctl status + default sink/source inspect (if available)
06_pw_dump.json         - raw pw-dump JSON (if available)
07_journal_roaring.txt  - recent roaring + carla journal
08_journal_pipewire.txt - recent pipewire/wireplumber journal
all_errors.txt          - all user-level errors (journalctl -p err..alert)
10_files_and_hashes.txt - bin listing + hashes of key scripts/config
files/                  - copies of referenced scripts/configs
11_hearth_repo.txt      - Hearth commit, working-tree status, entry points
hearth_dumps/           - Hearth state dumps (summary.txt, tracemalloc.pkl)
EOF

log "collecting systemd status..."
write_cmd "01_systemd_status.txt" bash -lc '
  echo "## systemctl --user status (key units)"
  systemctl --user --no-pager --full status \
    pipewire.service pipewire-pulse.service wireplumber.service \
    roaring-vm-sinks.service roaring-mic-busses.service roaring-mic-routesd.service \
    roaring-carla-session.service 2>&1 || true
  echo
  echo "## systemctl --user list-units (filtered)"
  systemctl --user --no-pager list-units --type=service 2>&1 | grep -E -i "roaring|pipewire|wireplumber|carla|lpd8" || true
'

log "collecting systemd show..."
write_cmd "02_systemd_show.txt" bash -lc '
  for u in \
    pipewire.service pipewire-pulse.service wireplumber.service \
    roaring-vm-sinks.service roaring-mic-busses.service roaring-mic-routesd.service \
    roaring-carla-session.service; do
    echo "## $u"
    systemctl --user show "$u" \
      -p ActiveState -p SubState -p ActiveEnterTimestamp \
      -p NRestarts -p ExecMainStatus -p Result -p MainPID 2>&1 || true
    echo
  done
'

log "collecting pactl..."
write_cmd "03_pactl_summary.txt" bash -lc '
  echo "## pactl info"
  pactl info 2>&1 || true
  echo
  echo "## pactl list short sinks"
  pactl list short sinks 2>&1 || true
  echo
  echo "## pactl list short sources"
  pactl list short sources 2>&1 || true
  echo
  echo "## pactl list short modules"
  pactl list short modules 2>&1 || true
'

write_cmd "04_pactl_full.txt" bash -lc '
  echo "## pactl list sinks"
  pactl list sinks 2>&1 || true
  echo
  echo "## pactl list sources"
  pactl list sources 2>&1 || true
'

if command -v wpctl >/dev/null 2>&1; then
  log "collecting wpctl..."
  write_cmd "05_wpctl.txt" bash -lc '
    echo "## wpctl status"
    wpctl status 2>&1 || true
    echo
    echo "## wpctl inspect default sink"
    id="$(wpctl status 2>/dev/null | awk "/Default Sink:/ {print $NF}" | tr -d ".")"
    [[ -n "${id:-}" ]] && wpctl inspect "$id" 2>&1 || true
    echo
    echo "## wpctl inspect default source"
    sid="$(wpctl status 2>/dev/null | awk "/Default Source:/ {print $NF}" | tr -d ".")"
    [[ -n "${sid:-}" ]] && wpctl inspect "$sid" 2>&1 || true
  '
fi

if command -v pw-dump >/dev/null 2>&1; then
  log "collecting pw-dump..."
  write_raw_cmd "06_pw_dump.json" pw-dump
fi

log "collecting journals (last 30 minutes)..."
write_cmd "07_journal_roaring.txt" journalctl --user --since "30 min ago" --no-pager \
  -u roaring-vm-sinks.service -u roaring-mic-busses.service -u roaring-mic-routesd.service \
  -u roaring-carla-session.service

write_cmd "08_journal_pipewire.txt" journalctl --user --since "30 min ago" --no-pager \
  -u pipewire.service -u pipewire-pulse.service -u wireplumber.service

log "collecting all errors..."
write_cmd "all_errors.txt" bash -lc '
  echo "## systemctl --user --failed"
  systemctl --user --no-pager --failed 2>&1 || true
  echo
  echo "## journalctl --user -p err..alert (last 2 hours)"
  journalctl --user -p err..alert --since "2 hours ago" --no-pager 2>&1 || true
'

log "collecting script hashes + configs..."
write_cmd "10_files_and_hashes.txt" bash -lc '
  set -e
  echo "## bin listing"
  ls -la "$HOME/bin" || true
  echo
  echo "## hashes (selected)"
  for f in \
    "$HOME/bin/roaring_audio_routesd.sh" \
    "$HOME/bin/roaring_vm_sinks.sh" \
    "$HOME/bin/roaring_mic_bussesd.sh" \
    "$HOME/bin/roaring_mic_routesd.sh" \
    "$HOME/bin/lpd8_mixer.sh" \
    "$HOME/bin/roaring_restart_everything.sh" \
    "$HOME/bin/roaring_audio_debug_dump.sh" \
    "$HOME/bin/hearth-mixer" \
    "$HOME/.config/hearth/roaring_mixer.conf" \
  ; do
    [[ -f "$f" ]] || continue
    sha256sum "$f" || true
  done
'

# Copy key files (so the bundle is self-contained)
write_file_if_exists "$HOME/bin/roaring_audio_routesd.sh"
write_file_if_exists "$HOME/bin/roaring_vm_sinks.sh"
write_file_if_exists "$HOME/bin/roaring_mic_bussesd.sh"
write_file_if_exists "$HOME/bin/roaring_mic_routesd.sh"
write_file_if_exists "$HOME/bin/lpd8_mixer.sh"
write_file_if_exists "$HOME/bin/roaring_restart_everything.sh"
write_file_if_exists "$HOME/bin/roaring_audio_debug_dump.sh"
write_file_if_exists "$HOME/bin/hearth-mixer"
write_file_if_exists "$HOME/.config/hearth/roaring_mixer.conf"
write_file_if_exists "$HOME/.config/systemd/user/roaring-carla-session.service"
write_file_if_exists "$HOME/.config/systemd/user/roaring-audio-routesd.service"
write_file_if_exists "$HOME/.config/systemd/user/roaring-mic-busses.service"
write_file_if_exists "$HOME/.config/systemd/user/roaring-mic-routesd.service"
write_file_if_exists "$HOME/.config/systemd/user/roaring-vm-sinks.service"
write_file_if_exists "$HOME/.config/systemd/user/lpd8-mixer.service"
write_file_if_exists "$HOME/bin/roaring_carla_launch.sh"
write_file_if_exists "$HOME/bin/start_roaring_and_carla.sh"
write_file_if_exists "$HOME/bin/roaring_toggle_astro_target.sh"
write_file_if_exists "$HOME/bin/toggle_scarlett_speakers.sh"
write_file_if_exists "$HOME/.config/systemd/user/default-sink-vm-game.service"
write_file_if_exists "$HOME/.config/hearth/roaring_mic_router.conf"

# Hearth provenance.  The app is a git repo now, not a single script, so the
# useful thing to carry is which commit is on disk -- not a gzipped copy of
# ~/bin/roaring_audio_control.py, which was retired in round 24.
HEARTH_REPO="$HOME/Documents/GitHub/Hearth"
if [[ -d "$HEARTH_REPO/.git" ]]; then
  {
    echo "## repo: $HEARTH_REPO"
    git -C "$HEARTH_REPO" log -1 --format='commit %H%nauthored %ai%nsubject %s' 2>&1 || true
    echo
    echo "## working tree (empty means clean)"
    git -C "$HEARTH_REPO" status --porcelain 2>&1 || true
    echo
    echo "## installed entry points"
    ls -la "$HOME/bin/hearth-mixer" "$HOME/bin/hearth.py" 2>&1 || true
  } > "$OUTDIR/11_hearth_repo.txt"
  log "included 11_hearth_repo.txt"
else
  log "Hearth repo not found at $HEARTH_REPO"
fi

# Hearth's own state dump, written by the 'dump' command on the control
# socket.  This replaces the old ~/roaring_vu_dump_*.csv.gz peak captures:
# --vu-dump and the PeakPoller CSV writer were removed in round 24, so that
# file is never produced any more and looking for it only ever misled.
HEARTH_DUMPS="${XDG_STATE_HOME:-$HOME/.local/state}/hearth/dumps"
if [[ -d "$HEARTH_DUMPS" ]]; then
  mkdir -p "$OUTDIR/hearth_dumps"
  cp -a "$HEARTH_DUMPS"/. "$OUTDIR/hearth_dumps/" 2>/dev/null || true
  log "included hearth_dumps/ from $HEARTH_DUMPS"
else
  log "no Hearth dumps at $HEARTH_DUMPS (run: hearth --dump, or 'dump' on the socket)"
fi


# ── Windows laptop state (via SSH) ──────────────────────────────────────────
WIN_IP="${WIN_IP:-192.168.50.132}"
WIN_USER="${WIN_USER:-roari}"
WIN_KEY="$HOME/.ssh/roaring_win"
WIN_OUT="$OUTDIR/09_windows_state.txt"

log "collecting Windows state via SSH..."
if ssh -i "$WIN_KEY" -o BatchMode=yes -o ConnectTimeout=5 "$WIN_USER@$WIN_IP" hostname >/dev/null 2>&1; then
  {
    echo "### Windows state collected at $(date --iso-8601=seconds)"
    echo "### target: $WIN_USER@$WIN_IP"
    echo ""

    echo "--- hostname / uptime ---"
    ssh -i "$WIN_KEY" -o BatchMode=yes "$WIN_USER@$WIN_IP" \
      "powershell -NoProfile -NonInteractive -Command \"hostname; (Get-Date); (Get-CimInstance Win32_OperatingSystem).LastBootUpTime\""

    echo ""
    echo "--- RoaringMic process ---"
    ssh -i "$WIN_KEY" -o BatchMode=yes "$WIN_USER@$WIN_IP" \
      "powershell -NoProfile -NonInteractive -Command \"Get-Process powershell -EA SilentlyContinue | Select Id, CPU, StartTime | Sort StartTime | Format-Table -Auto\""

    echo ""
    echo "--- Port 46001 (WaveRouter receiver) ---"
    ssh -i "$WIN_KEY" -o BatchMode=yes "$WIN_USER@$WIN_IP" \
      "powershell -NoProfile -NonInteractive -Command \"netstat -an | Select-String '46001'\""

    echo ""
    echo "--- waveOut / audio devices ---"
    ssh -i "$WIN_KEY" -o BatchMode=yes "$WIN_USER@$WIN_IP" \
      "powershell -NoProfile -NonInteractive -Command \"Get-CimInstance Win32_SoundDevice | Select Name, Status | Format-Table -Auto\""

    echo ""
    echo "--- CABLE device ---"
    ssh -i "$WIN_KEY" -o BatchMode=yes "$WIN_USER@$WIN_IP" \
      "powershell -NoProfile -NonInteractive -Command \"Get-CimInstance Win32_SoundDevice | Select Name, Status | Format-Table -Auto\""

    echo ""
    echo "--- Sunshine process ---"
    ssh -i "$WIN_KEY" -o BatchMode=yes "$WIN_USER@$WIN_IP" \
      "powershell -NoProfile -NonInteractive -Command \"Get-Process sunshine -EA SilentlyContinue | Select Id, CPU, StartTime\""

    echo ""
    echo "--- RoaringMic AppDir ---"
    ssh -i "$WIN_KEY" -o BatchMode=yes "$WIN_USER@$WIN_IP" \
      "powershell -NoProfile -NonInteractive -Command \"dir C:\Users\roari\AppData\Local\RoaringMic | Select Name, LastWriteTime, Length | Format-Table -Auto\""

    echo ""
    echo "--- Firewall SSH rule ---"
    ssh -i "$WIN_KEY" -o BatchMode=yes "$WIN_USER@$WIN_IP" \
      "powershell -NoProfile -NonInteractive -Command \"Get-NetFirewallRule -DisplayName '*SSH*' | Select DisplayName, Enabled, Direction, Action | Format-Table -Auto\""

  } > "$WIN_OUT" 2>&1 || true || true
  echo "[debug-dump] Windows state collected -> $(basename "$WIN_OUT")"
else
  echo "### Windows SSH unreachable at $WIN_IP -- skipped" > "$WIN_OUT"
  echo "[debug-dump] WARNING: could not reach Windows laptop -- skipped"
fi
log "packing $TAR ..."
tar -czf "$TAR" -C "$(dirname "$OUTDIR")" "$(basename "$OUTDIR")"

log "done -> $TAR"
echo "$TAR"
