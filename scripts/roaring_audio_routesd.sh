#!/usr/bin/env bash
set -euo pipefail

# 10/1/2026-2  fix:pactl-storm-1
# Summary: Keeps VM audio routed to Astro target via module-loopback.
#          FIX: ONE sinks + ONE modules list per 5s iteration (was ~4 pactl/s).
# 5/13/2026-3  restore_mic_listen guardian
# Summary: After each iteration, check ~/.cache/roaring_mic_listen/{b1,b2}.ids.
#          If the state file says ON but the loopback is gone (reboot/PW restart),
#          auto-restore it to the current Astro target.

LOG="$HOME/.cache/roaring-audio-routesd.log"
mkdir -p "$HOME/.cache"
exec >>"$LOG" 2>&1

CONF="$HOME/.config/hearth/roaring_mixer.conf"
STATE_FILE="$HOME/.cache/roaring-audio-routesd.target"

HEARTH_REPO="${HEARTH_REPO:-$HOME/Documents/GitHub/Hearth}"
ENV_LATENCY_MSEC="${LATENCY_MSEC:-40}"
ENV_ASTRO_TARGET="${ASTRO_TARGET:-}"

log() { echo "[audio-routesd] $(date +%H:%M:%S) $*"; }
now_sec() { date +%s; }

load_conf() {
    LATENCY_MSEC="$ENV_LATENCY_MSEC"
    ASTRO_TARGET="$ENV_ASTRO_TARGET"
    # runtime-provided conf path; contents are trusted operator config (SC1090).
    # shellcheck source=/dev/null
    if [[ -f "$CONF" ]]; then
        source "$CONF"
    fi
}

wait_for_pactl() {
    local delay=0.5 max=4.0
    until pactl info >/dev/null 2>&1; do
        sleep "$delay"
        delay="$(awk -v d="$delay" -v m="$max" 'BEGIN{x=d*1.5; printf "%.1f", (x>m)?m:x}')"
    done
}

# ── Batched pactl caches ───────────────────────────────────────────────────────
_sinks_cache=""
_modules_cache=""

refresh_sinks()   { _sinks_cache="$(pactl list short sinks 2>/dev/null)"; }
refresh_modules() { _modules_cache="$(pactl list short modules 2>/dev/null)"; }

sink_in_cache() { printf '%s\n' "$_sinks_cache" | awk '{print $2}' | grep -Fqx -- "$1"; }

# ── Astro target detection against cached sink list ────────────────────────────
find_astro_target() {
    # Normal desktop playback is pinned to the configured Astro Chat sink.
    # Never infer a destination from a generic "Astro" match: that can select
    # the spatialized Game endpoint when Chat is temporarily unavailable.
    if [[ -z "${ASTRO_TARGET:-}" ]]; then
        log "ERROR: ASTRO_TARGET is unset; refusing automatic endpoint selection" >&2
        return 1
    fi
    if ! sink_in_cache "$ASTRO_TARGET"; then
        log "UNAVAILABLE: configured Astro Chat sink is absent: $ASTRO_TARGET; refusing fallback" >&2
        return 1
    fi
    printf '%s\n' "$ASTRO_TARGET"
}

unload_vm_loopbacks_to_sink() {
    local sink="$1"
    # Exact token comparisons only. Do not unload similarly named sources
    # such as vm_game.monitor-extra or sinks that merely contain the target.
    printf '%s\n' "$_modules_cache" | awk -v k="$sink" '
        $2=="module-loopback" {
            src=""; dst=""
            for (i=3; i<=NF; i++) {
                if ($i ~ /^source=/) src=substr($i,8)
                if ($i ~ /^sink=/) dst=substr($i,6)
            }
            if ((src=="vm_game.monitor" || src=="vm_chat.monitor" || src=="vm_music.monitor") && dst==k) print $1
        }
    ' | while read -r id; do
        [[ -n "${id:-}" ]] || continue
        pactl unload-module "$id" >/dev/null 2>&1 || true
    done
}

# ── Route-health guardian ──────────────────────────────────────────────────
# Run the same typed, tested graph verifier used by Hearth. It batches all
# three VM buses, refuses ambiguous/unknown routes, and only reconnects exact
# missing links on a uniquely identified loopback node.
ensure_vm_routes() {
    local sink="$1" output line
    if [[ ! -f "$HEARTH_REPO/src/hearth/audio/route_guard.py" ]]; then
        log "ERROR: Hearth route-health module is missing: $HEARTH_REPO/src/hearth/audio/route_guard.py"
        return 0
    fi

    if ! output="$(
        PYTHONSAFEPATH=1 PYTHONPATH="$HEARTH_REPO/src" \
            python3 -m hearth.audio.route_guard --sink "$sink" --latency "$LATENCY_MSEC" 2>&1
    )"; then
        log "ERROR: route-health helper failed: $output"
        return 0
    fi

    while IFS= read -r line; do
        [[ -n "$line" ]] || continue
        # Healthy is the normal steady state; log only transitions, warnings,
        # unavailable inventory, and actual recovery actions.
        [[ "$line" == *": healthy ("* ]] && continue
        log "$line"
    done <<< "$output"
    refresh_modules
}

# ── Mic listen (sidetone) guardian ────────────────────────────────────────────
# toggle_mic_listen.sh stores module IDs in ~/.cache/roaring_mic_listen/{b1,b2}.ids.
# After a PipeWire restart the modules are gone but the state files remain, so
# the sidetone silently disappears until the user manually re-toggles.
# This function runs each iteration and re-creates any loopback whose state file
# says ON but whose modules are no longer present.
MIC_LISTEN_STATE_DIR="$HOME/.cache/roaring_mic_listen"
restore_mic_listen() {
    local target="$1"
    local bus src state id mods
    mods="$_modules_cache"
    for bus in b1 b2; do
        state="$MIC_LISTEN_STATE_DIR/${bus}.ids"
        [[ -f "$state" ]] || continue
        src="mic_${bus}.monitor"
        # Check if any stored ID is still a live loopback for this source
        local live=0
        while read -r id; do
            [[ -n "${id:-}" ]] || continue
            if printf '%s\n' "$mods" | awk -v i="$id" -v s="$src" \
                'BEGIN{f=0} $1==i && $2=="module-loopback" && $0~("source="s){f=1} END{exit !f}'; then
                live=1; break
            fi
        done < "$state"
        if (( live == 0 )); then
            # Stale — re-create loopback and update state file
            id="$(pactl load-module module-loopback \
                source="$src" sink="$target" latency_msec=18 rate=48000 \
                channels=2 remix=yes \
                source_dont_move=true sink_dont_move=true 2>/dev/null || true)"
            if [[ -n "${id:-}" ]]; then
                printf '%s\n' "$id" > "$state"
                log "restored mic listen $bus: $src -> $target (id=$id)"
                refresh_modules
            else
                rm -f "$state"
                log "FAILED restore mic listen $bus: $src -> $target"
            fi
        fi
    done
}

main() {
    load_conf
    log "starting (Chat-only route health guardian, LATENCY=${LATENCY_MSEC}ms)"
    while true; do
        wait_for_pactl
        load_conf

        # Read sinks for exact Chat-only selection. The Python route guard
        # batches fresh sink/module/link inventories once for all VM buses.
        refresh_sinks

        local target=""
        if target="$(find_astro_target)"; then
            :
        else
            log "configured Astro Chat target unavailable; preserving existing graph and retrying"
            sleep 5
            continue
        fi

        local last=""
        last="$(cat "$STATE_FILE" 2>/dev/null || true)"
        if [[ -n "$last" && "$last" != "$target" ]]; then
            log "target changed: $last -> $target; removing only exact old VM loopbacks"
            refresh_modules
            unload_vm_loopbacks_to_sink "$last"
        fi
        [[ "$last" != "$target" ]] && printf '%s\n' "$target" > "$STATE_FILE"

        ensure_vm_routes "$target"
        restore_mic_listen "$target"

        sleep 5    # was 2 — reduced from ~4 pactl/s to ~0.4/s
    done
}

main
