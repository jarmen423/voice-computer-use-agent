"""Unit tests for the closed-loop computer-use behavior in VisionBridge."""

from unittest.mock import AsyncMock

import pytest

from voiceuse.config import Config
from voiceuse.models import MonitorInfo
from voiceuse.vision_bridge import VisionBridge


class FakeOSController:
    """Small OSController stand-in that records local UI actions."""

    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.screenshots: list[str] = []

    def list_monitors(self) -> list[MonitorInfo]:
        """Return one deterministic monitor for coordinate translation tests."""
        return [MonitorInfo(index=1, name="primary", rect=(10, 20, 800, 600), is_primary=True)]

    def screenshot_monitor(self, monitor_index: int, output_path: str) -> str:
        """Record screenshot captures without touching the real desktop."""
        self.screenshots.append(output_path)
        return output_path

    def click(self, x: int, y: int) -> None:
        """Record translated global click coordinates."""
        self.clicks.append((x, y))

    def type_text(self, text: str) -> None:
        """Stub typing for supported loop actions."""

    def press_key(self, key: str) -> None:
        """Stub key presses for supported loop actions."""


@pytest.mark.asyncio
async def test_find_and_click_reobserves_until_done() -> None:
    """The visual loop should execute an action, capture again, then accept done."""
    fake_os = FakeOSController()
    bridge = VisionBridge(config=Config(), os_controller=fake_os)  # type: ignore[arg-type]
    bridge._call_computer_use_provider = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "success": True,
                "action": "click",
                "x": 100,
                "y": 120,
                "confidence": 0.95,
                "message": "Click the visible submit button.",
            },
            {
                "success": True,
                "action": "done",
                "message": "The submit action completed.",
            },
        ]
    )

    result = await bridge.find_and_click("submit button")

    assert result.success is True
    assert result.message == "The submit action completed."
    assert fake_os.clicks == [(110, 140)]
    assert len(fake_os.screenshots) == 2


@pytest.mark.asyncio
async def test_find_and_click_blocks_low_confidence_click() -> None:
    """Low-confidence visual clicks should stop before pyautogui is called."""
    fake_os = FakeOSController()
    bridge = VisionBridge(config=Config(), os_controller=fake_os)  # type: ignore[arg-type]
    bridge._call_computer_use_provider = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "success": True,
            "action": "click",
            "x": 100,
            "y": 120,
            "confidence": 0.2,
            "message": "This is uncertain.",
        }
    )

    result = await bridge.find_and_click("submit button")

    assert result.success is False
    assert "Low click confidence" in result.message
    assert fake_os.clicks == []
