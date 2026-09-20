#!/usr/bin/env bash
set -euo pipefail

# 4/17/2026-1
# Summary: Streams laptop_audio.monitor → Windows host over UDP (stereo s16le).
#          Mirrors roaring_moonlight_micd.sh pattern exactly.

LOG="$HOME/.cache/roaring-laptop-audiod.log"
mkdir -p "$HOME/.cache"
# 41,316 resampler-glitch lines had grown this to 7MB. Keep the recent tail
# and drop the rest, so the log stays readable and bounded without needing a
# logrotate rule for one daemon.
LOG_MAX_BYTES="${LOG_MAX_BYTES:-1048576}"
if [[ -f "$LOG" ]] && (($(stat -c %s "$LOG" 2>/dev/null || echo 0) > LOG_MAX_BYTES)); then
    tail -n 400 "$LOG" >"${LOG}.tmp" 2>/dev/null && mv -f "${LOG}.tmp" "$LOG"
fi
exec >>"$LOG" 2>&1

WINDOWS_IP="${LAPTOP_HOST_IP:-}"
AUDIO_SOURCE="${AUDIO_SOURCE:-laptop_audio.monitor}"
AUDIO_PORT="${AUDIO_PORT:-46000}"
RATE=48000
CHANNELS=2
RETRY_SEC=3
# Recycle ffmpeg after this many resampler glitches (see MEMORY note below).
GLITCH_LIMIT="${GLITCH_LIMIT:-3}"
# A timestamp delta this large is not drift, it is the machine having been
# asleep. Bridging it is hopeless, so the stream is recycled on the first one
# rather than after GLITCH_LIMIT. See the WEDGE note in stream_audio().
JUMP_SEC="${JUMP_SEC:-5}"
MAX_BACKOFF_SEC=60

log() { echo "[laptop-audiod] $(date '+%Y-%m-%d %H:%M:%S') $*"; }

# ffmpeg's pid, global so the signal handler can reach it.
FF_PID=""
FIFO=""

# systemd stops this unit by signalling the whole cgroup. A wedged ffmpeg
# (see the WEDGE note) cannot service SIGTERM, so without this the stop ran
# to TimeoutStopSec, Fedora's timeout-abort drop-in then sent SIGABRT, and
# every stop cost a 300MB coredump and ~100s of CPU. SIGKILL is the only
# signal a wedged decode thread cannot ignore.
on_signal() {
    trap - TERM INT
    [[ -n "$FF_PID" ]] && kill -9 "$FF_PID" 2>/dev/null
    [[ -n "$FIFO" ]] && rm -f "$FIFO"
    log "stopped on signal"
    exit 0
}
trap on_signal TERM INT

if [[ -z "$WINDOWS_IP" ]]; then
    log "FATAL: LAPTOP_HOST_IP not set in service Environment="
    exit 1
fi

wait_for_pactl() {
    local delay=0.5 max=4.0
    until pactl info >/dev/null 2>&1; do
        sleep "$delay"
        delay="$(awk -v d="$delay" -v m="$max" 'BEGIN{x=d*1.5; printf "%.1f", (x>m)?m:x}')"
    done
}

wait_for_source() {
    local delay=0.5 max=4.0
    until pactl list short sources 2>/dev/null | awk '{print $2}' | grep -qx "$AUDIO_SOURCE"; do
        wait_for_pactl
        sleep "$delay"
        delay="$(awk -v d="$delay" -v m="$max" 'BEGIN{x=d*1.5; printf "%.1f", (x>m)?m:x}')"
    done
    log "source ready: $AUDIO_SOURCE"
}

stream_audio() {
    # NOTE: -use_wallclock_as_timestamps was removed (same bug as moonlight).
    # It substitutes jittery wall-clock for PulseAudio timestamps, causing
    # non-monotonic DTS spam that balloons the log file (was 8MB+).
    # aresample=async=1 handles real clock drift cleanly.
    #
    # MEMORY: when the source suspends/resumes, PulseAudio's PTS jumps by the
    # whole idle gap and libswresample tries to bridge it with silence. It logs
    # "Failed to compensate for timestamp delta" and grows an internal buffer
    # that it never releases for the life of the process. Measured on this rig:
    # a healthy sender is a flat 74.7MB, but one 9-error burst added 124MB and
    # 4.5 days of them reached 1.8GB resident+swap -- per stream. So we watch
    # stderr and recycle ffmpeg once a burst starts; the outer loop restarts it.
    #
    # WEDGE: the deltas that actually show up here are 6-8 HOURS
    # (22164.006583 s and friends), which is not clock drift -- it is the PC
    # having been suspended overnight while ffmpeg held the source open. On
    # resume libswresample tries to bridge the whole gap with real silence:
    # 22164 s x 48 kHz x 4 B is over 4 GB of samples, generated in a filter
    # thread that never returns to check for signals. That is the hang behind
    # `Failed with result 'timeout'`: the process is not deadlocked, it is
    # busy manufacturing six hours of silence. So a jump is recycled on sight
    # and killed with SIGKILL, because SIGTERM will not be looked at.
    local fifo
    fifo="$(mktemp -u "${XDG_RUNTIME_DIR:-/tmp}/laptop-audiod.XXXXXX")"
    mkfifo -m 600 "$fifo" || return 1
    FIFO="$fifo"

    ffmpeg \
        -f pulse \
        -i "$AUDIO_SOURCE" \
        -af aresample=async=1:min_hard_comp=0.1 \
        -ar "$RATE" \
        -ac "$CHANNELS" \
        -f s16le \
        -fflags +nobuffer \
        "udp://${WINDOWS_IP}:${AUDIO_PORT}?pkt_size=1316" \
        -loglevel error \
        2>"$fifo" &
    FF_PID=$!
    local ff="$FF_PID"

    local glitches=0 delta
    while IFS= read -r line; do
        case "$line" in
            *"Failed to compensate for timestamp delta"*)
                glitches=$((glitches + 1))
                delta="${line##*delta of }"
                delta="${delta%%.*}"
                [[ "$delta" =~ ^[0-9]+$ ]] || delta=0
                # Only the first of a burst is logged. Bursts used to arrive
                # thousands at a time and were the entire reason the log was
                # 7MB of one repeated sentence.
                if ((glitches == 1)); then
                    log "ffmpeg: $line"
                fi
                if ((delta >= JUMP_SEC)); then
                    log "clock jumped ${delta}s (resume from sleep) - killing ffmpeg, the stream will restart"
                    kill -9 "$ff" 2>/dev/null || true
                    break
                fi
                if ((glitches >= GLITCH_LIMIT)); then
                    log "resampler glitch x${glitches} - recycling ffmpeg to release its buffers"
                    kill -9 "$ff" 2>/dev/null || true
                    break
                fi
                ;;
            *)
                log "ffmpeg: $line"
                ;;
        esac
    done <"$fifo"

    local rc=0
    wait "$ff" || rc=$?
    FF_PID=""
    rm -f "$fifo"
    FIFO=""
    return "$rc"
}

# Each stream_audio() run makes a private stderr FIFO and deletes it on the way
# out, but a SIGKILL (or a cgroup OOM kill) skips that cleanup and leaves the
# node behind in XDG_RUNTIME_DIR. Only one instance of this daemon ever runs,
# so anything matching the pattern at startup is stale by definition.
rm -f "${XDG_RUNTIME_DIR:-/tmp}"/laptop-audiod.?????? 2>/dev/null || true

log "starting — source=${AUDIO_SOURCE} → udp://${WINDOWS_IP}:${AUDIO_PORT} (${RATE}Hz stereo)"
backoff="$RETRY_SEC"
while true; do
    wait_for_pactl
    wait_for_source
    started=$SECONDS
    stream_audio || true
    ran=$((SECONDS - started))
    # A healthy stream runs for hours. If it keeps dying immediately the fault
    # is persistent, so back off instead of spinning on it.
    if [[ $ran -lt 30 ]]; then
        backoff=$((backoff * 2))
        [[ $backoff -gt $MAX_BACKOFF_SEC ]] && backoff="$MAX_BACKOFF_SEC"
    else
        backoff="$RETRY_SEC"
    fi
    log "stream ended after ${ran}s — retrying in ${backoff}s..."
    sleep "$backoff"
done
