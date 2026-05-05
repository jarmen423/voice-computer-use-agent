"""Tests for focused operating-system side-effect services."""

from __future__ import annotations

from voiceuse import os_services
from voiceuse.models import WindowInfo
from voiceuse.os_services import InputSimulator, ScreenshotService, SystemCommandExecutor


class FakePyAutoGUI:
    """Minimal pyautogui stand-in for input simulation tests."""

    def __init__(self) -> None:
        self.typed: list[str] = []
        self.hotkeys: list[tuple[str, ...]] = []
        self.clicks: list[tuple[int, int]] = []
        self.keys: list[str] = []

    def typewrite(self, text: str, interval: float = 0.0) -> None:
        """Record ASCII typing requests."""
        self.typed.append(text)

    def hotkey(self, *keys: str) -> None:
        """Record paste hotkeys."""
        self.hotkeys.append(keys)

    def click(self, x: int, y: int) -> None:
        """Record click coordinates."""
        self.clicks.append((x, y))

    def press(self, key: str) -> None:
        """Record key presses."""
        self.keys.append(key)


class FakeClipboard:
    """Small pyperclip replacement that stores text in memory."""

    def __init__(self) -> None:
        self.value = "old"

    def paste(self) -> str:
        """Return the current fake clipboard text."""
        return self.value

    def copy(self, text: str) -> None:
        """Store fake clipboard text."""
        self.value = text


class FakeScreenshot:
    """Minimal MSS screenshot object."""

    rgb = b"rgb"
    size = (1, 1)


class FakeMSS:
    """Context-manager fake for MSS monitor/window capture."""

    def __init__(self) -> None:
        self.monitors = [
            {"left": 0, "top": 0, "width": 100, "height": 100},
            {"left": 0, "top": 0, "width": 100, "height": 100},
        ]
        self.grabbed_regions: list[dict[str, int]] = []

    def __enter__(self) -> "FakeMSS":
        """Enter fake context manager."""
        return self

    def __exit__(self, *args) -> None:
        """Exit fake context manager."""

    def grab(self, region: dict[str, int]) -> FakeScreenshot:
        """Record the requested capture region."""
        self.grabbed_regions.append(region)
        return FakeScreenshot()


def test_input_simulator_uses_typewrite_for_ascii() -> None:
    """ASCII text should keep the direct keyboard typing path."""
    fake_gui = FakePyAutoGUI()
    simulator = InputSimulator(fake_gui)

    simulator.type_text("hello")

    assert fake_gui.typed == ["hello"]
    assert fake_gui.hotkeys == []


def test_input_simulator_pastes_unicode_text(monkeypatch) -> None:
    """Non-ASCII text should use clipboard paste instead of pyautogui.typewrite."""
    fake_gui = FakePyAutoGUI()
    fake_clipboard = FakeClipboard()
    monkeypatch.setattr(os_services, "pyperclip", fake_clipboard)
    simulator = InputSimulator(fake_gui)

    simulator.type_text("cafe \u00e9lan")

    assert fake_gui.typed == []
    assert fake_gui.hotkeys == [("ctrl", "v")]
    assert fake_clipboard.value == "old"


def test_system_command_executor_blocks_compound_syntax() -> None:
    """Even allowed command names should reject shell metacharacters."""
    executor = SystemCommandExecutor()

    result = executor.execute("echo hello; whoami")

    assert result.success is False
    assert "compound" in result.message.lower()


def test_screenshot_service_captures_window_region(monkeypatch, tmp_path) -> None:
    """ScreenshotService should translate WindowInfo rectangles into MSS regions."""
    fake_mss = FakeMSS()
    writes: list[str] = []

    monkeypatch.setattr(
        ScreenshotService,
        "_write_png",
        staticmethod(lambda screenshot, output_path: writes.append(output_path)),
    )
    service = ScreenshotService(lambda: fake_mss)
    output_path = str(tmp_path / "window.png")

    result = service.screenshot_window(
        WindowInfo(title="Chrome", pid=1, rect=(10, 20, 300, 200), monitor_index=1),
        output_path,
    )

    assert result == output_path
    assert fake_mss.grabbed_regions == [{"left": 10, "top": 20, "width": 300, "height": 200}]
    assert writes == [output_path]
