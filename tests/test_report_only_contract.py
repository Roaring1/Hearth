"""Fake contract harness only; does not exercise or certify the production watchdog."""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fakes"))
from report_only_contract import ReportOnlyAdapter


class ForbiddenHostActions:
    def __init__(self):
        self.calls = []

    def restart(self, *args, **kwargs):
        self.calls.append(("restart", args, kwargs))
        raise AssertionError("REPORT-ONLY contract must never restart a host service")

    def recover(self, *args, **kwargs):
        self.calls.append(("recover", args, kwargs))
        raise AssertionError("REPORT-ONLY contract must never invoke recovery")

    def notify(self, *args, **kwargs):
        self.calls.append(("notify", args, kwargs))
        raise AssertionError("REPORT-ONLY contract must never notify")


def test_report_only_collects_fake_sample_without_host_action_calls():
    actions = ForbiddenHostActions()
    fake_sample = {
        "verdict": "INCONCLUSIVE",
        "active_windows": 2,
        "valid_windows": 1,
        "reason": "fixture",
    }
    # Host-action API is not injectable into this adapter contract.
    assert not {"actions", "host_actions", "recovery", "restart"} & set(
        inspect.signature(ReportOnlyAdapter).parameters
    )
    adapter = ReportOnlyAdapter(lambda: fake_sample)
    report = adapter.run_once()
    assert report == fake_sample
    assert actions.calls == []


def test_report_only_log_is_bounded():
    adapter = ReportOnlyAdapter(lambda: {"verdict": "NO_SAMPLE", "reason": "fixture"}, log_limit=3)
    for _ in range(7):
        adapter.run_once()
    assert len(adapter.log) == 3
