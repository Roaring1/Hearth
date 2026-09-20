#!/usr/bin/env bash
# roaring_carla_patch.sh
# Connects sm7b_mono -> Carla audio inputs after Carla starts, and -- only
# when CARLA_TO_B2 says so -- routes Carla's processed output into mic_b2
# as a second processed endpoint rather than a raw pre-Carla tap.
#
# Carla's .carxp patchbay restore references SM7B:input_FL / SM7B:monitor_FL
# which don't exist as PipeWire ports on the sm7b_mono remap-source node
# (it only has capture_MONO). This script creates the correct links manually.
set -euo pipefail

# Whether Carla's processed output also feeds mic_b2 is now the user's
# choice, stored as CARLA_TO_B2 in the mic router conf and toggled from
# Hearth's IN menu. It used to be on unconditionally, so b2 was a copy of
# b1 that nobody had asked for and nothing in the UI could refuse.
# The old B2_PROCESSED env var still wins when set, for one-off runs.
# Safe by design: mic_b2.monitor only feeds b2_mic + the VU meter, never
# Carla's inputs, so tapping Carla:out -> mic_b2 creates no feedback loop.
MIC_ROUTER_CONF="${MIC_ROUTER_CONF:-$HOME/.config/hearth/roaring_mic_router.conf}"
CARLA_TO_B2="false"
if [[ -r "$MIC_ROUTER_CONF" ]]; then
  # shellcheck disable=SC1090 # operator config, path provided at runtime
  source "$MIC_ROUTER_CONF"
fi
case "${CARLA_TO_B2,,}" in
  1 | true | yes | on) conf_b2=1 ;;
  *) conf_b2=0 ;;
esac
B2_PROCESSED="${B2_PROCESSED:-$conf_b2}"

log() { echo "[carla-patch] $(date +'%H:%M:%S') $*"; }

try_link() {
  local out="$1" in="$2" label="$3"
  if pw-link "$out" "$in" 2>/dev/null; then
    log "linked $label"
  else
    log "$label already linked or failed"
  fi
}

wait_for_node() {
  local name="$1" max=30 i=0
  until pw-cli ls Node 2>/dev/null | grep -q "node.name = \"${name}\""; do
    ((i++)) || true
    if (( i > max )); then
      log "timeout waiting for node: ${name}"
      exit 1
    fi
    sleep 0.5
  done
  log "node ready: ${name}"
}

# Non-fatal variant: returns 1 on timeout instead of exiting, so an optional
# node (mic_b2) missing at boot doesn't abort the whole patch.
wait_for_node_soft() {
  local name="$1" max="${2:-20}" i=0
  until pw-cli ls Node 2>/dev/null | grep -q "node.name = \"${name}\""; do
    ((i++)) || true
    if (( i > max )); then
      log "timeout waiting for node: ${name} (soft)"
      return 1
    fi
    sleep 0.5
  done
  return 0
}

wait_for_port() {
  local node="$1" port="$2" max=20 i=0
  until pw-link -l 2>/dev/null | grep -q "^  ${port}$" || \
        pw-dump 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
nodes={o['id']:o for o in d if o.get('type')=='PipeWire:Interface:Node'}
ports=[o for o in d if o.get('type')=='PipeWire:Interface:Port']
target_nid=next((nid for nid,n in nodes.items() if n.get('info',{}).get('props',{}).get('node.name')=='${node}'),None)
if not target_nid: sys.exit(1)
found=any(p.get('info',{}).get('props',{}).get('node.id')==target_nid and p.get('info',{}).get('props',{}).get('port.name')=='${port}' for p in ports)
sys.exit(0 if found else 1)
" 2>/dev/null; do
    ((i++)) || true
    if (( i > max )); then
      log "timeout waiting for ${node}:${port}"
      return 1
    fi
    sleep 0.5
  done
}

# b2 = second processed bus: route Carla's output into mic_b2 as well.
link_b2_processed() {
  if [[ "$B2_PROCESSED" != "1" ]]; then
    log "B2_PROCESSED=0; leaving b2 raw/unrouted"
    return 0
  fi
  if ! wait_for_node_soft "mic_b2"; then
    log "mic_b2 not present yet; skipping b2 processed link"
    return 0
  fi
  log "linking Carla output -> mic_b2 (b2 = second processed bus)"
  try_link Carla:audio-out1 mic_b2:playback_FL "audio-out1 -> mic_b2:playback_FL"
  try_link Carla:audio-out2 mic_b2:playback_FR "audio-out2 -> mic_b2:playback_FR"
}

main() {
  log "waiting for sm7b_mono and Carla..."
  wait_for_node "sm7b_mono"
  wait_for_node "Carla"

  # Give Carla a moment to fully initialize its ports
  sleep 1

  log "linking sm7b_mono -> Carla inputs"
  try_link sm7b_mono:capture_MONO Carla:audio-in1 "audio-in1"
  try_link sm7b_mono:capture_MONO Carla:audio-in2 "audio-in2"

  link_b2_processed

  log "done"
}

# Only auto-run when executed directly; allows sourcing functions in tests.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main
fi
