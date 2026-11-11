"""Event-driven mpv frame-content sampling for active Capture Mode.

The Lua probe runs inside the owned mpv process, observes ``time-pos`` events,
and samples a small pixel grid from ``screenshot-raw`` only while a capture
session is active. It writes only coarse health metadata to mpv's private
``user-data`` property; raw pixels and screenshots are never persisted.
"""

from __future__ import annotations

import math
import os
import stat
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from hearth.errors import HearthError

FRAME_HEALTH_LUA = """-- Hearth's session-scoped, event-driven frame-content probe.
local SAMPLE_INTERVAL = 2.0
local BLACK_AFTER = 6.0
local UNCHANGED_AFTER = 10.0
local GRID_WIDTH = 32
local GRID_HEIGHT = 18
local BLACK_CHANNEL_MAX = 8
local BLACK_FRACTION_MIN = 0.98
local UINT32 = 4294967296

local last_sample_at = 0
local last_fingerprint = nil
local unchanged_since = nil
local black_since = nil
local sample_count = 0

local function publish(state, width, height, black_fraction, unchanged_s, black_s)
    mp.set_property_native("user-data/hearth/frame-health", {
        ready = true,
        state = state,
        width = width or 0,
        height = height or 0,
        sample_count = sample_count,
        black_fraction = black_fraction or 0,
        unchanged_s = unchanged_s or 0,
        black_s = black_s or 0,
    })
end

local function publish_unknown()
    last_fingerprint = nil
    unchanged_since = nil
    black_since = nil
    publish("unknown", 0, 0, 0, 0, 0)
end

local function sample(now)
    local ok, image = pcall(mp.command_native, {"screenshot-raw", "video", "bgr0"})
    if not ok or type(image) ~= "table" or type(image.data) ~= "string" then
        publish_unknown()
        return
    end

    local width = image.w
    local height = image.h
    local stride = image.stride
    local data = image.data
    if image.format ~= "bgr0"
        or type(width) ~= "number" or type(height) ~= "number"
        or type(stride) ~= "number" or width < 1 or height < 1
        or width % 1 ~= 0 or height % 1 ~= 0 or stride % 1 ~= 0
        or stride < width * 4 or #data < stride * height then
        publish_unknown()
        return
    end

    local fingerprint = 5381
    local black_pixels = 0
    local total_pixels = GRID_WIDTH * GRID_HEIGHT
    for row = 0, GRID_HEIGHT - 1 do
        local y = math.min(height - 1, math.floor((row + 0.5) * height / GRID_HEIGHT))
        for column = 0, GRID_WIDTH - 1 do
            local x = math.min(width - 1, math.floor((column + 0.5) * width / GRID_WIDTH))
            local index = y * stride + x * 4 + 1
            local blue, green, red = string.byte(data, index, index + 2)
            if red == nil then
                publish_unknown()
                return
            end
            if red <= BLACK_CHANNEL_MAX
                and green <= BLACK_CHANNEL_MAX
                and blue <= BLACK_CHANNEL_MAX then
                black_pixels = black_pixels + 1
            end
            fingerprint = (fingerprint * 33 + blue) % UINT32
            fingerprint = (fingerprint * 33 + green) % UINT32
            fingerprint = (fingerprint * 33 + red) % UINT32
        end
    end

    sample_count = sample_count + 1
    local black_fraction = black_pixels / total_pixels
    local is_black = black_fraction >= BLACK_FRACTION_MIN
    if fingerprint == last_fingerprint then
        unchanged_since = unchanged_since or now
    else
        last_fingerprint = fingerprint
        unchanged_since = now
    end
    if is_black then
        black_since = black_since or now
    else
        black_since = nil
    end

    local unchanged_s = math.max(0, now - (unchanged_since or now))
    local black_s = math.max(0, now - (black_since or now))
    local state = "changing"
    if black_s >= BLACK_AFTER then
        state = "mostly_black"
    elseif unchanged_s >= UNCHANGED_AFTER then
        state = "unchanged"
    end
    publish(state, width, height, black_fraction, unchanged_s, black_s)
end

local function on_time_pos(_, value)
    if type(value) ~= "number" then
        return
    end
    local now = mp.get_time()
    if now - last_sample_at < SAMPLE_INTERVAL then
        return
    end
    last_sample_at = now
    sample(now)
end

publish("waiting_for_video", 0, 0, 0, 0, 0)
mp.observe_property("time-pos", "number", on_time_pos)
"""


class FrameHealthError(HearthError):
    """The private frame-health script could not be created safely."""


@dataclass(frozen=True, slots=True)
class FrameHealthSnapshot:
    """Validated, content-free frame health metadata received over mpv IPC."""

    ready: bool
    state: str
    width: int
    height: int
    sample_count: int
    black_fraction: float
    unchanged_s: float
    black_s: float


def parse_frame_health_payload(value: object) -> FrameHealthSnapshot | None:
    """Validate the small Lua health object; reject malformed/untrusted IPC data."""
    if not isinstance(value, Mapping):
        return None
    ready = value.get("ready")
    state = value.get("state")
    width = value.get("width")
    height = value.get("height")
    sample_count = value.get("sample_count")
    black_fraction = value.get("black_fraction")
    unchanged_s = value.get("unchanged_s")
    black_s = value.get("black_s")
    if (
        not isinstance(ready, bool)
        or not isinstance(state, str)
        or state
        not in {
            "waiting_for_video",
            "changing",
            "mostly_black",
            "unchanged",
            "unknown",
        }
    ):
        return None
    if isinstance(width, bool) or not isinstance(width, int) or width < 0:
        return None
    if isinstance(height, bool) or not isinstance(height, int) or height < 0:
        return None
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 0:
        return None
    if isinstance(black_fraction, bool) or not isinstance(black_fraction, (int, float)):
        return None
    if isinstance(unchanged_s, bool) or not isinstance(unchanged_s, (int, float)):
        return None
    if isinstance(black_s, bool) or not isinstance(black_s, (int, float)):
        return None
    black_fraction_value = float(black_fraction)
    unchanged_value = float(unchanged_s)
    black_value = float(black_s)
    if not all(
        math.isfinite(item) and item >= 0
        for item in (black_fraction_value, unchanged_value, black_value)
    ):
        return None
    if black_fraction_value > 1:
        return None
    if state not in {"waiting_for_video", "unknown"} and (
        width < 1 or height < 1 or sample_count < 1
    ):
        return None
    return FrameHealthSnapshot(
        ready=ready,
        state=state,
        width=width,
        height=height,
        sample_count=sample_count,
        black_fraction=black_fraction_value,
        unchanged_s=unchanged_value,
        black_s=black_value,
    )


def write_frame_health_script(runtime_directory: Path) -> Path:
    """Write the Lua probe privately beside mpv's session-only IPC socket."""
    directory = runtime_directory.resolve()
    try:
        metadata = directory.stat()
    except OSError as exc:
        raise FrameHealthError(f"viewer runtime directory cannot be inspected: {exc}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise FrameHealthError("viewer runtime directory is not private and owner-controlled")
    script_path = directory / "frame-health.lua"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)
    fd: int | None = None
    created = False
    try:
        fd = os.open(script_path, flags, 0o600)
        created = True
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None  # fdopen owns and closes the descriptor from here.
            stream.write(FRAME_HEALTH_LUA)
            stream.flush()
            os.fsync(stream.fileno())
        script_path.chmod(0o600)
    except OSError as exc:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)
        if created:
            with suppress(OSError):
                script_path.unlink()
        raise FrameHealthError(f"could not create private frame-health script: {exc}") from exc
    return script_path
