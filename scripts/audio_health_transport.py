"""Dependency-free PipeWire transport snapshot comparison helpers.

Cumulative ERR counters are compared only for matching node identities.
Unknown, reset or recreated nodes are baselines, never fabricated XRUN deltas.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_NODE_MARKERS = {
    "scarlett": "alsa_input.usb-Focusrite_Scarlett_Solo",
    "carla": "Carla",
    "b1": "b1_mic",
}


def parse_pw_top(text: str) -> dict[str, dict[str, Any]]:
    """Parse pw-top rows, preserving timing columns as text to avoid unit assumptions."""
    result: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10 or fields[0] not in {"E", "S", "I", "R", "t", "T", "!"}:
            continue
        try:
            node_id = int(fields[1])
            quantum = int(fields[2])
            rate = int(fields[3])
            errors = int(fields[8])
        except (ValueError, IndexError):
            continue

        for key, marker in _NODE_MARKERS.items():
            if marker not in line:
                continue
            node_name = "input.b1_mic" if key == "b1" and "input.b1_mic" in line else marker
            candidate: dict[str, Any] = {
                "id": node_id,
                "status": fields[0],
                "quantum": quantum,
                "rate": rate,
                "wait": fields[4],
                "busy": fields[5],
                "wq": fields[6],
                "bq": fields[7],
                "errors": errors,
                "name": node_name,
            }
            previous = result.get(key)
            if previous is None:
                result[key] = candidate
                continue

            same_node = node_id == previous.get("id")
            if not same_node:
                if (key == "b1" and node_name == "input.b1_mic") or (
                    fields[0] == "R" and previous.get("status") != "R"
                ):
                    result[key] = candidate
                continue

            # ERR is cumulative across the batch. Keep the maximum for this
            # identity while refreshing live timing fields from the latest
            # running row. Zero QUANT/RATE placeholders must not erase a
            # non-zero node value captured in another batch row.
            merged = dict(previous)
            merged["errors"] = max(errors, int(previous.get("errors", 0)))
            if fields[0] == "R":
                merged["status"] = "R"
                for field in ("wait", "busy", "wq", "bq"):
                    value = candidate[field]
                    if value not in {"---", "???"}:
                        merged[field] = value
                for field in ("quantum", "rate"):
                    if candidate[field] > 0:
                        merged[field] = candidate[field]
            if key == "b1" and node_name == "input.b1_mic":
                merged["name"] = node_name
            result[key] = merged
    return result


def snapshot_for_state(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only bounded, JSON-safe transport metadata for persistent state."""
    nodes = snapshot.get("nodes")
    safe_nodes: dict[str, dict[str, Any]] = {}
    if isinstance(nodes, Mapping):
        for key, value in nodes.items():
            if not isinstance(key, str) or not isinstance(value, Mapping):
                continue
            safe_nodes[key] = {
                field: value.get(field)
                for field in (
                    "id",
                    "name",
                    "status",
                    "errors",
                    "quantum",
                    "rate",
                    "wait",
                    "busy",
                    "wq",
                    "bq",
                )
            }
    errors = snapshot.get("errors", [])
    return {
        "sampled_at_unix": snapshot.get("sampled_at_unix"),
        "sampled_at_monotonic": snapshot.get("sampled_at_monotonic"),
        "pw_top_ok": bool(snapshot.get("pw_top_ok")),
        "metadata_ok": bool(snapshot.get("metadata_ok")),
        "quantum": snapshot.get("quantum"),
        "rate": snapshot.get("rate"),
        "errors": list(errors)[:8] if isinstance(errors, (list, tuple)) else [],
        "nodes": safe_nodes,
    }


def compare_snapshots(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare same-identity node counters and graph settings; never guess on reset."""
    if not isinstance(before, Mapping):
        return {
            "status": "baseline",
            "reason": "No prior compatible transport snapshot",
            "elapsed_seconds": None,
            "node_deltas": {},
            "identity_changes": [],
            "counter_resets": [],
            "configuration_changes": [],
        }

    before_nodes = before.get("nodes")
    after_nodes = after.get("nodes")
    if not isinstance(before_nodes, Mapping) or not isinstance(after_nodes, Mapping):
        return {
            "status": "unavailable",
            "reason": "Node inventory missing from one or both snapshots",
            "elapsed_seconds": _elapsed(before, after),
            "node_deltas": {},
            "identity_changes": [],
            "counter_resets": [],
            "configuration_changes": [],
        }

    node_deltas: dict[str, int] = {}
    identity_changes: list[str] = []
    counter_resets: list[str] = []
    configuration_changes: list[str] = []
    comparable = 0

    for key in sorted(set(before_nodes) | set(after_nodes)):
        old = before_nodes.get(key)
        new = after_nodes.get(key)
        if not isinstance(old, Mapping) or not isinstance(new, Mapping):
            identity_changes.append(f"{key}: node absent in one snapshot")
            continue
        old_id, new_id = old.get("id"), new.get("id")
        if not isinstance(old_id, int) or not isinstance(new_id, int) or old_id != new_id:
            identity_changes.append(f"{key}: node identity changed ({old_id!r}->{new_id!r})")
            continue
        old_errors, new_errors = old.get("errors"), new.get("errors")
        if not isinstance(old_errors, int) or not isinstance(new_errors, int):
            continue
        comparable += 1
        if bool(before.get("pw_top_ok")) and bool(after.get("pw_top_ok")):
            if new_errors < old_errors:
                counter_resets.append(f"{key}: ERR counter reset ({old_errors}->{new_errors})")
            elif new_errors > old_errors:
                node_deltas[key] = new_errors - old_errors

        for field in ("quantum", "rate"):
            if old.get(field) != new.get(field):
                configuration_changes.append(
                    f"{key}: {field} changed ({old.get(field)!r}->{new.get(field)!r})"
                )

    for field in ("quantum", "rate"):
        if before.get(field) != after.get(field):
            configuration_changes.append(
                f"graph {field} changed ({before.get(field)!r}->{after.get(field)!r})"
            )

    pw_top_ok = bool(before.get("pw_top_ok")) and bool(after.get("pw_top_ok"))
    metadata_ok = bool(before.get("metadata_ok")) and bool(after.get("metadata_ok"))
    if not pw_top_ok:
        status = "partial" if comparable or identity_changes else "unavailable"
        reason = "One or both pw-top queries failed; no new ERR deltas are trusted"
    elif comparable == 0:
        status = "partial" if identity_changes else "unavailable"
        reason = "No same-identity node counters were available"
    elif identity_changes or counter_resets:
        status = "partial"
        reason = "One or more node identities/counters changed; only same-node deltas are trusted"
    elif not metadata_ok:
        status = "partial"
        reason = "ERR counters are comparable, but one or both clock-metadata queries failed"
    else:
        status = "available"
        reason = "Compared cumulative ERR counters for matching node identities"

    return {
        "status": status,
        "reason": reason,
        "elapsed_seconds": _elapsed(before, after),
        "node_deltas": node_deltas,
        "identity_changes": identity_changes,
        "counter_resets": counter_resets,
        "configuration_changes": configuration_changes,
    }


def _elapsed(before: Mapping[str, Any], after: Mapping[str, Any]) -> float | None:
    start = before.get("sampled_at_monotonic")
    end = after.get("sampled_at_monotonic")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        return round(float(end - start), 3)
    return None
