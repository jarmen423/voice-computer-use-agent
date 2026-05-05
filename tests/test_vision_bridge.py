"""Unit tests for the closed-loop computer-use behavior in VisionBridge."""

from unittest.mock import AsyncMock
from types import SimpleNamespace

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


def test_capture_target_reuses_short_lived_cache_and_force_refreshes() -> None:
    """Rapid repeated captures can reuse a screenshot, while force gets a new one."""
    fake_os = FakeOSController()
    bridge = VisionBridge(config=Config(), os_controller=fake_os)  # type: ignore[arg-type]

    first = bridge._capture_target(app_name=None)
    cached = bridge._capture_target(app_name=None)
    refreshed = bridge._capture_target(app_name=None, force=True)

    assert first.screenshot_path == cached.screenshot_path
    assert refreshed.screenshot_path != first.screenshot_path
    assert len(fake_os.screenshots) == 2


@pytest.mark.asyncio
async def test_anthropic_action_parses_type_tool(tmp_path) -> None:
    """Anthropic computer-use type actions should map to the local type action."""
    fake_os = FakeOSController()
    config = Config()
    config.computer_use.provider = "anthropic"
    bridge = VisionBridge(config=config, os_controller=fake_os)  # type: ignore[arg-type]
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"png")
    target = bridge._capture_target(app_name=None)
    target.screenshot_path = str(screenshot)
    bridge._get_anthropic_client = lambda: SimpleNamespace(  # type: ignore[method-assign]
        messages=SimpleNamespace(
            create=lambda **_: SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        input={"action": "type", "text": "hello"},
                    )
                ]
            )
        )
    )

    action = await bridge._call_anthropic_action("type hello", target, [])

    assert action["success"] is True
    assert action["action"] == "type"
    assert action["text"] == "hello"


@pytest.mark.asyncio
async def test_anthropic_action_parses_key_tool(tmp_path) -> None:
    """Anthropic computer-use key actions should map to the local key action."""
    fake_os = FakeOSController()
    config = Config()
    config.computer_use.provider = "anthropic"
    bridge = VisionBridge(config=config, os_controller=fake_os)  # type: ignore[arg-type]
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"png")
    target = bridge._capture_target(app_name=None)
    target.screenshot_path = str(screenshot)
    bridge._get_anthropic_client = lambda: SimpleNamespace(  # type: ignore[method-assign]
        messages=SimpleNamespace(
            create=lambda **_: SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        input={"action": "key", "text": "enter"},
                    )
                ]
            )
        )
    )

    action = await bridge._call_anthropic_action("press enter", target, [])

    assert action["success"] is True
    assert action["action"] == "key"
    assert action["key"] == "enter"
