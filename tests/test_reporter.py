"""Tests for the background reporter thread.

None of these need PipeWire, a display or systemd: ``status.gather`` is
replaced, which is the point of the reporter depending on one function.
"""

from __future__ import annotations

import threading

import pytest

from hearth import chain, health, mic, reporter, share, status


def make_report(problem: str | None = None) -> status.Report:
    """A report that is either clean or carrying one named problem.

    The headline is derived from the issues rather than stored, so varying
    the report means varying what is actually wrong -- which is how the real
    thing behaves too.
    """
    issues = (
        [] if problem is None else [health.Issue(key="test", severity="critical", title=problem)]
    )
    return status.Report(
        health=health.Health(issues=issues),
        chain=chain.ChainState(present=False),
        share=share.ShareView(installed=False),
        mic=mic.MicView(recording=False, analysis=None, session_count=0),
    )


def test_an_unchanged_report_is_not_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    """A health surface that repaints "all is well" every few seconds is a
    health surface people stop reading."""
    monkeypatch.setattr(status, "gather", lambda **_: make_report())
    rep = reporter.Reporter(lambda _report: None)

    assert rep.poll_once() is not None  # the first answer is always news
    assert rep.poll_once() is None
    assert rep.poll_once() is None


def test_a_changed_report_is_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    problems = iter([None, "Music is muted"])
    monkeypatch.setattr(status, "gather", lambda **_: make_report(next(problems)))
    rep = reporter.Reporter(lambda _report: None)

    first = rep.poll_once()
    second = rep.poll_once()
    assert first is not None and second is not None
    assert second.headline == "Music is muted"


def test_a_failed_gather_is_not_reported_as_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure and "no change" are both ``None`` to a caller, so a failure
    must not leave state that makes the recovered report look like news that
    never arrived -- or worse, like nothing ever went wrong."""

    def boom(**_: object) -> status.Report:
        raise RuntimeError("pactl went away")

    monkeypatch.setattr(status, "gather", boom)
    rep = reporter.Reporter(lambda _report: None)

    assert rep.poll_once() is None
    assert rep.last is None

    monkeypatch.setattr(status, "gather", lambda **_: make_report())
    assert rep.poll_once() is not None


def test_last_is_available_to_a_window_built_mid_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    """A panel created after the thread started must paint at once."""
    monkeypatch.setattr(status, "gather", lambda **_: make_report("Music is muted"))
    rep = reporter.Reporter(lambda _report: None)

    assert rep.last is None
    rep.poll_once()
    last = rep.last
    assert last is not None
    assert last.headline == "Music is muted"


def test_refresh_now_insists_on_a_fresh_share_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Right after the user changes what is shared, the cached answer is
    exactly the wrong one to show."""
    ages: list[float] = []

    def record(*, max_share_age: float = status.SHARE_TTL) -> status.Report:
        ages.append(max_share_age)
        return make_report()

    monkeypatch.setattr(status, "gather", record)
    rep = reporter.Reporter(lambda _report: None)

    rep.poll_once()
    rep.refresh_now()
    rep.poll_once()
    rep.poll_once()

    assert ages == [status.SHARE_TTL, 0.0, status.SHARE_TTL]


def test_a_broken_consumer_does_not_kill_the_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every panel is fed by this one thread; a bad callback must not take
    the rest of them down with it."""
    problems = iter(["one", "two", "three"])
    monkeypatch.setattr(status, "gather", lambda **_: make_report(next(problems)))
    seen: list[str] = []
    delivered = threading.Event()

    def consumer(report: status.Report) -> None:
        seen.append(report.headline)
        if len(seen) >= 3:
            delivered.set()
        raise RuntimeError("the window exploded")

    rep = reporter.Reporter(consumer, visible_interval=0.01, hidden_interval=0.01)
    rep.start()
    try:
        assert delivered.wait(timeout=5.0), f"thread stopped after {seen}"
    finally:
        rep.stop()
    assert seen[:3] == ["one", "two", "three"]


def test_hidden_windows_poll_far_less_often() -> None:
    """A hidden window has no reader; paying the probe cost for it is waste."""
    rep = reporter.Reporter(lambda _report: None)
    rep.set_window_visible(False)
    hidden = rep._delay()
    rep.set_window_visible(True)
    visible = rep._delay()
    assert hidden > visible


def test_stop_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    """The IPC server taught this lesson: a thread that outlives stop() is a
    shutdown bug that only shows up in production."""
    monkeypatch.setattr(status, "gather", lambda **_: make_report())
    rep = reporter.Reporter(lambda _report: None, visible_interval=30.0, hidden_interval=30.0)
    rep.start()
    rep.stop(timeout=5.0)
    assert not rep.is_alive()
