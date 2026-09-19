"""Tests for the microphone suite wrapper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hearth import mic

ROUTER_CONF = """# 9/1/2026-1
# Summary:
# - Controls which raw mic(s) feed B1 and B2.
#   Options per bus: none | sm7b | astro | both

B1_ROUTE="both"
B2_ROUTE="astro"

# loopback stability knobs
LATENCY_MSEC="10"
RATE="48000"

B1_ACTIVE="false"

B2_ACTIVE="false"
"""


def write_session(root: Path, name: str, summary: dict[str, object] | None) -> Path:
    session = root / mic.SESSION_ROOT_NAME / f"{mic.SESSION_PREFIX}{name}"
    session.mkdir(parents=True)
    if summary is not None:
        analysis = session / "analysis"
        analysis.mkdir()
        (analysis / "summary.json").write_text(json.dumps(summary))
    return session


def clean_summary(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "generated_utc": "2026-09-19T06:00:00Z",
        "duration_s": 600.0,
        "raw": {
            "noise_floor_dbfs": -68.4,
            "peak_dbfs": -6.1,
            "voice_mean_rms_dbfs": -21.0,
            "clip_event_count": 0,
            "hum_findings": [],
        },
        "post": {
            "noise_floor_dbfs": -70.2,
            "peak_dbfs": -1.2,
            "clip_event_count": 0,
            "hum_findings": [],
        },
        "chain_effect": {"gain_applied_db": 12.5, "dynamic_range_reduction_db": 4.0},
        "flags": [],
    }
    base.update(over)
    return base


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(mic.paths, "home", lambda: tmp_path)
    monkeypatch.setattr(mic.paths, "config_dir", lambda: tmp_path / ".config" / "hearth")
    return tmp_path


def test_no_recordings_is_a_designed_state_not_an_error(home: Path) -> None:
    """This rig has never recorded a session, so this is the real first run."""
    view = mic.view(recording=False)
    assert view.session_count == 0
    assert view.analysis is None
    assert "No microphone recordings yet" in view.summary
    assert "start the recorder" in view.summary


def test_recording_with_no_results_yet_explains_the_wait(home: Path) -> None:
    view = mic.view(recording=True)
    assert "Recording now" in view.summary


def test_sessions_are_newest_first_by_name(home: Path) -> None:
    write_session(home, "20260101T000000Z", None)
    write_session(home, "20260919T120000Z", None)
    write_session(home, "20260601T000000Z", None)
    found = mic.sessions()
    assert [p.name for p in found] == [
        f"{mic.SESSION_PREFIX}20260919T120000Z",
        f"{mic.SESSION_PREFIX}20260601T000000Z",
        f"{mic.SESSION_PREFIX}20260101T000000Z",
    ]


def test_a_clean_recording_says_so(home: Path) -> None:
    write_session(home, "20260919T060000Z", clean_summary())
    analysis = mic.latest_analysis()
    assert analysis is not None
    assert analysis.headline == "Your microphone sounded clean"
    assert analysis.raw.noise_floor_dbfs == -68.4
    assert analysis.gain_applied_db == 12.5


def test_clipping_is_reported_in_plain_words(home: Path) -> None:
    summary = clean_summary()
    raw = dict(summary["raw"])  # type: ignore[arg-type]
    raw["clip_event_count"] = 3
    summary["raw"] = raw
    write_session(home, "20260919T060000Z", summary)
    analysis = mic.latest_analysis()
    assert analysis is not None
    assert analysis.headline == "Your microphone is clipping"


def test_a_noisy_room_is_reported_with_the_number(home: Path) -> None:
    summary = clean_summary()
    summary["raw"] = {"noise_floor_dbfs": -41.2, "clip_event_count": 0, "hum_findings": []}
    write_session(home, "20260919T060000Z", summary)
    analysis = mic.latest_analysis()
    assert analysis is not None
    assert analysis.headline == "Background noise is high (-41 dBFS)"


def test_high_severity_flags_win_and_are_sorted(home: Path) -> None:
    summary = clean_summary(
        flags=[
            {"severity": "low", "key": "minor", "message": "Something small. Details here."},
            {
                "severity": "high",
                "key": "gate_chatter",
                "message": "The noise gate is chattering. More.",
            },
        ]
    )
    write_session(home, "20260919T060000Z", summary)
    analysis = mic.latest_analysis()
    assert analysis is not None
    assert analysis.flags[0].severity == "high"
    assert analysis.headline == "The noise gate is chattering"


def test_the_newest_unanalysed_session_does_not_hide_older_results(home: Path) -> None:
    """The in-flight segment has no summary yet; that must not read as 'nothing'."""
    write_session(home, "20260919T060000Z", clean_summary())
    write_session(home, "20260919T061000Z", None)
    analysis = mic.latest_analysis()
    assert analysis is not None
    assert analysis.name == "20260919T060000Z"
    assert mic.view(recording=True).session_count == 2


def test_a_half_written_summary_degrades_instead_of_raising(home: Path) -> None:
    session = write_session(home, "20260919T060000Z", clean_summary())
    (session / "analysis" / "summary.json").write_text('{"raw": {"noise_floor')
    analysis = mic.latest_analysis()
    assert analysis is not None
    assert analysis.flags == []
    assert analysis.raw.noise_floor_dbfs is None


def test_missing_dashboard_is_normal(home: Path) -> None:
    write_session(home, "20260919T060000Z", clean_summary())
    analysis = mic.latest_analysis()
    assert analysis is not None
    assert analysis.dashboard is None


def test_routes_are_read_from_the_shell_config(home: Path) -> None:
    conf_dir = home / ".config" / "hearth"
    conf_dir.mkdir(parents=True)
    (conf_dir / mic.ROUTER_CONF).write_text(ROUTER_CONF)
    assert mic.routes() == {"B1": "both", "B2": "astro"}


def test_routes_are_described_without_internal_names(home: Path) -> None:
    lines = mic.describe_routes({"B1": "both", "B2": "astro"})
    assert lines == ["Stream: Both mics", "Discord: Headset mic only"]
    assert not any("B1" in line or "B2" in line for line in lines)


def test_an_unknown_route_value_is_shown_rather_than_dropped(home: Path) -> None:
    assert mic.describe_routes({"B1": "experimental"}) == ["Stream: experimental"]


def test_missing_router_config_is_empty_not_fatal(home: Path) -> None:
    assert mic.routes() == {}
    assert mic.describe_routes({}) == []
