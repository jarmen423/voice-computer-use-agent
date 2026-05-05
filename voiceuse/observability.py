"""Lightweight runtime observability helpers for VoiceUse.

Voice agents feel broken when they are slow, even if each subsystem eventually
returns. These helpers emit consistent latency logs around pipeline stages and
tool calls so local runs can answer where time went: STT, LLM planning, tool
execution, or TTS.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("voiceuse.observability")


@dataclass(frozen=True)
class TimingRecord:
    """One completed timing measurement."""

    name: str
    elapsed_ms: float
    success: bool
    detail: str = ""


class LatencyTimer:
    """Measure elapsed wall-clock time for one operation."""

    def __init__(self, name: str, detail: str = "") -> None:
        self.name = name
        self.detail = detail
        self._started_at = time.perf_counter()

    def finish(self, *, success: bool = True, detail: Optional[str] = None) -> TimingRecord:
        """Finish the timer, log it, and return the measurement."""
        elapsed_ms = (time.perf_counter() - self._started_at) * 1000
        record = TimingRecord(
            name=self.name,
            elapsed_ms=elapsed_ms,
            success=success,
            detail=self.detail if detail is None else detail,
        )
        logger.info(
            "latency name=%s success=%s elapsed_ms=%.1f detail=%s",
            record.name,
            record.success,
            record.elapsed_ms,
            record.detail,
        )
        return record
