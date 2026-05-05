"""Unit tests for OSController command execution policy."""

from voiceuse.config import Config
from voiceuse.models import WindowInfo
from voiceuse.os_controller import OSController


def test_execute_system_blocks_interpreters() -> None:
    """Interpreter prefixes must not bypass safety with arbitrary code."""
    controller = OSController(config=Config())

    result = controller.execute_system('python -c "print(123)"')

    assert result.success is False
    assert "blocked" in result.message.lower()


def test_execute_system_blocks_shell_mode() -> None:
    """Raw shell execution is disabled even when a caller asks for it."""
    controller = OSController(config=Config())

    result = controller.execute_system("echo hello", allow_shell=True)

    assert result.success is False
    assert "disabled" in result.message.lower()


def test_list_windows_uses_short_ttl_cache(monkeypatch) -> None:
    """Repeated window lookups should not enumerate the OS every time."""
    controller = OSController(config=Config())
    controller.platform = "windows"
    calls = {"count": 0}

    def fake_windows():
        calls["count"] += 1
        return [WindowInfo(title="Chrome", pid=1, rect=(0, 0, 100, 100), monitor_index=1)]

    monkeypatch.setattr(controller, "_list_windows_windows", fake_windows)

    assert controller.list_windows()[0].title == "Chrome"
    assert controller.list_windows()[0].title == "Chrome"
    assert calls["count"] == 1

    controller.invalidate_window_cache()
    assert controller.list_windows()[0].title == "Chrome"
    assert calls["count"] == 2
