"""Finishing an evaluation is not the same as completing the robot's task."""
from html.parser import HTMLParser

import pytest

from robot_worlds.report import render_report


class Status(HTMLParser):
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.capture = attrs.get("id") == "run-status"
        if self.capture:
            self.status = attrs["class"]
    def handle_data(self, data):
        if getattr(self, "capture", False):
            self.label = data


@pytest.mark.parametrize("episode,status,label", [
    ({"status": "completed", "rubric": {"status": "passed", "success": True}}, "passed", "Task completed"),
    ({"status": "completed", "rubric": {"status": "failed", "success": False}}, "failed", "Task incomplete"),
    ({"status": "cancelled", "rubric": {"status": "passed", "success": True}}, "invalid", "Attempt interrupted or invalid"),
    ({"status": "completed"}, "unscorable", "Insufficient evidence"),
])
def test_report_header_distinguishes_task_verdict_from_executor_status(episode, status, label):
    report = render_report({"status": "completed", "episodes": [episode]}, {"name": "Factory"})
    parsed = Status()
    parsed.feed(report)
    assert parsed.status == "pill " + status
    assert parsed.label == label
    assert "badge($('run-status')" not in report  # JS must not overwrite the truthful initial header.
