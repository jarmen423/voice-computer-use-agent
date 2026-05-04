"""Unit tests for voiceuse.models."""

import pytest
from voiceuse.models import CommandResult, MonitorInfo, ToolCall, VoiceCommand, WindowInfo


def test_command_result_defaults() -> None:
    """CommandResult data field should default to None."""
    result = CommandResult(success=True, message="ok")
    assert result.success is True
    assert result.message == "ok"
    assert result.data is None


def test_window_info_defaults() -> None:
    """WindowInfo should allow default hwnd and is_active values."""
    win = WindowInfo(title="Test", pid=123, rect=(0, 0, 100, 100), monitor_index=1)
    assert win.hwnd is None
    assert win.is_active is False


def test_monitor_info_primary_default() -> None:
    """MonitorInfo is_primary should default to False."""
    mon = MonitorInfo(index=1, name="main", rect=(0, 0, 1920, 1080))
    assert mon.is_primary is False


def test_tool_call_creation() -> None:
    """ToolCall should store tool name and parameters."""
    tc = ToolCall(tool_name="open_app", parameters={"app_name": "chrome"})
    assert tc.tool_name == "open_app"
    assert tc.parameters == {"app_name": "chrome"}


def test_voice_command_defaults() -> None:
    """VoiceCommand should provide sensible defaults."""
    vc = VoiceCommand(raw_text="open chrome")
    assert vc.intent is None
    assert vc.tool_calls == []
    assert vc.requires_confirmation is False
    assert vc.confirmation_prompt is None
