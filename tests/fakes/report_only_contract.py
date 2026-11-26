"""Test-only REPORT-ONLY adapter contract; does not call production or host services."""

from collections import deque


class ReportOnlyAdapter:
    def __init__(self, sample_source, log_limit=4):
        self._sample_source = sample_source
        self.log = deque(maxlen=log_limit)

    def run_once(self):
        sample = self._sample_source()
        report = {
            key: sample.get(key) for key in ("verdict", "active_windows", "valid_windows", "reason")
        }
        self.log.append(report.copy())
        return report
