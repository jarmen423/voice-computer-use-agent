"""Tests for lightweight latency logging helpers."""

from __future__ import annotations

from voiceuse.observability import LatencyTimer


def test_latency_timer_returns_record() -> None:
    """Finishing a timer should produce a structured measurement."""
    timer = LatencyTimer("test.operation", detail="start")

    record = timer.finish(success=True, detail="done")

    assert record.name == "test.operation"
    assert record.success is True
    assert record.detail == "done"
    assert record.elapsed_ms >= 0
