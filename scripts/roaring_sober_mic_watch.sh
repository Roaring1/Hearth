#!/usr/bin/env bash
# Watch for Sober capture streams and route them to b1_mic via PipeWire metadata.
# Runs as a persistent service.
#
# Gap #7: the original loop ran `sleep 2` at the TOP of every iteration, so
# there was always up to a 2s window in which Sober recorded to the wrong
# source (b2_mic) before routing kicked in. Now:
#   * the first check runs IMMEDIATELY (0s latency when Sober is already up),
#   * the poll interval is configurable via SOBER_WATCH_POLL (default 0.5s),
# cutting worst-case latency from 2s to 0.5s without the fragility of a
# long-lived `pw-metadata --monitor` subprocess that would be far harder to
# supervise and restart cleanly under systemd.

set -euo pipefail

POLL="${SOBER_WATCH_POLL:-0.5}"

log() { echo "[sober-mic-watch] $(date +'%H:%M:%S') $*"; }

# One pw-dump per pass, parsed once, printing "<b1_serial> <sober_node_id>".
#
# This used to be two functions, each running its own `pw-dump | python3`.
# At the 0.5s poll that was four process spawns every second, forever, and
# it showed: pw-dump was caught at 80% CPU with the parser behind it at 60%.
# The graph is the same graph for both answers, so it is dumped once.
scan() {
    pw-dump 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
serial = node = ''
for obj in data:
    if obj.get('type') != 'PipeWire:Interface:Node':
        continue
    p = obj.get('info', {}).get('props', {})
    name = p.get('node.name')
    if not serial and name == 'b1_mic':
        serial = str(p.get('object.serial', ''))
    elif not node and name == 'Sober' and 'Input' in p.get('media.class', ''):
        node = str(obj['id'])
    if serial and node:
        break
print(serial, node)
" 2>/dev/null || true
}

get_metadata_target() {
    local node_id="$1"
    # Output format: update: id:N key:'target.object' value:'VAL' type:'...'
    # Split on ' gives: $1=prefix $2=target.object $3= value: $4=VAL
    pw-metadata -n default 2>/dev/null \
        | awk -F"'" "/id:${node_id} key:'target.object'/ {print \$4}" \
        || true
}

# Is Sober even running? One pgrep costs microseconds; a pw-dump of the
# whole graph costs tens of milliseconds of CPU and allocates a JSON
# document. Sober is shut most of the day, so the expensive question is
# only asked once the cheap one says yes. The poll interval is unchanged,
# so the routing latency Gap #7 was about is unchanged too.
sober_running() {
    # Pure builtins: no fork at all. pgrep twice a second was itself
    # measurable (it walks all of /proc) at ~4% of a core. A running
    # flatpak owns a numeric instance directory whose `info` names the
    # app; the per-app-id directory next to it is NOT a liveness signal,
    # it survives the app exiting.
    local info line
    for info in "${XDG_RUNTIME_DIR:-/run/user/$UID}"/.flatpak/[0-9]*/info; do
        [[ -r "$info" ]] || continue
        while IFS= read -r line; do
            case "$line" in
            name=org.vinegarhq.Sober) return 0 ;;
            name=*) break ;;
            esac
        done <"$info"
    done
    return 1
}

route_once() {
    local b1_serial sober_id current
    if ! sober_running; then return 0; fi
    read -r b1_serial sober_id <<<"$(scan)"
    if [[ -z "${b1_serial:-}" ]]; then return 0; fi
    if [[ -z "${sober_id:-}" ]]; then return 0; fi

    current="$(get_metadata_target "$sober_id")"
    if [[ "$current" == "$b1_serial" ]]; then return 0; fi

    log "routing Sober (node $sober_id) -> b1_mic (serial $b1_serial)"
    # if/then/else, not A && B || C: `log done` could itself fail under
    # `set -e` and wrongly trigger the failure branch (SC2015).
    if pw-metadata -n default "$sober_id" target.object "$b1_serial" \
            >/dev/null 2>&1; then
        log "done"
    else
        log "pw-metadata failed"
    fi
}

main() {
    log "started (poll ${POLL}s, graph only while Sober is running)"
    local first=1
    while true; do
        # Check immediately on the first pass (no startup latency); sleep only
        # between subsequent passes.
        if [[ "$first" == "1" ]]; then first=0; else sleep "$POLL"; fi
        route_once
    done
}

# Only auto-run when executed directly; allows sourcing functions in tests.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main
fi
