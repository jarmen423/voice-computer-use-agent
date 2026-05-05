"""Tests for focused operating-system side-effect services."""

from __future__ import annotations

from voiceuse import os_services
from voiceuse.os_services import InputSimulator, SystemCommandExecutor


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
