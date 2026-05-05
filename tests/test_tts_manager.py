"""Tests for cancellable text-to-speech queue behavior."""

from __future__ import annotations

import pytest

from voiceuse.config import Config
from voiceuse.tts_manager import TTSManager


class FakeProcess:
    """Minimal subprocess stand-in used to verify playback termination."""

    def __init__(self) -> None:
        self.returncode = None
        self.terminated = False

    def terminate(self) -> None:
        """Record that the manager asked the playback process to stop."""
        self.terminated = True
        self.returncode = -15


@pytest.mark.asyncio
async def test_speak_with_interrupt_clears_stale_queue() -> None:
    """Interrupting speech should discard older queued phrases."""
    manager = TTSManager(Config())

    await manager.speak("Opening Chrome.")
    await manager.speak("Done.", interrupt=True)

    queued = manager._queue.get_nowait()
    assert queued == "Done."
    assert manager._queue.empty()


@pytest.mark.asyncio
async def test_cancel_terminates_active_subprocess() -> None:
    """Cancellation should stop subprocess-backed playback immediately."""
    manager = TTSManager(Config())
    fake_process = FakeProcess()
    manager._current_process = fake_process  # type: ignore[assignment]

    await manager.cancel()

    assert fake_process.terminated is True
    assert manager._cancel_event.is_set()
