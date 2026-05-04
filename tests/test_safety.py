"""Unit tests for voiceuse.safety."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from voiceuse.config import Config, SafetyConfig
from voiceuse.models import ToolCall
from voiceuse.safety import SafetyGuard, SafetyCheckResult


@pytest.fixture
def default_guard() -> SafetyGuard:
    """Return a SafetyGuard with default destructive keywords."""
    config = Config(safety=SafetyConfig(confirm_destructive=True))
    return SafetyGuard(config)


class TestCheckCommand:
    """Tests for SafetyGuard.check_command."""

    def test_safe_command(self, default_guard: SafetyGuard) -> None:
        """A benign tool call should be marked safe."""
        tc = ToolCall(tool_name="open_app", parameters={"app_name": "chrome"})
        result = default_guard.check_command(tc)
        assert result.is_safe is True
        assert result.requires_confirmation is False

    def test_execute_system_always_flagged(self, default_guard: SafetyGuard) -> None:
        """execute_system should always require confirmation regardless of params."""
        tc = ToolCall(tool_name="execute_system", parameters={"command": "echo hello"})
        result = default_guard.check_command(tc)
        assert result.is_safe is False
        assert result.requires_confirmation is True

    def test_destructive_keyword_in_params(self, default_guard: SafetyGuard) -> None:
        """A destructive keyword in parameters should flag the call."""
        tc = ToolCall(tool_name="type_text", parameters={"text": "please delete everything"})
        result = default_guard.check_command(tc)
        assert result.is_safe is False
        assert result.requires_confirmation is True

    def test_destructive_keyword_in_raw_text(self, default_guard: SafetyGuard) -> None:
        """A destructive keyword in raw_text should flag the call."""
        tc = ToolCall(tool_name="open_app", parameters={"app_name": "files"})
        result = default_guard.check_command(tc, raw_text="shutdown the computer")
        assert result.is_safe is False

    def test_case_insensitive_matching(self, default_guard: SafetyGuard) -> None:
        """Keyword matching should be case-insensitive."""
        tc = ToolCall(tool_name="type_text", parameters={"text": "DELETE files"})
        result = default_guard.check_command(tc)
        assert result.is_safe is False


class TestConfirm:
    """Tests for SafetyGuard.confirm async flow."""

    @pytest.mark.asyncio
    async def test_affirmative_response(self, default_guard: SafetyGuard) -> None:
        """User saying 'yes' should return True."""
        tts = MagicMock()
        tts.speak = MagicMock(return_value=None)
        get_text = AsyncMock(return_value="yes")
        confirmed = await default_guard.confirm(tts, get_text, "Are you sure?")
        assert confirmed is True
        get_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_negative_response(self, default_guard: SafetyGuard) -> None:
        """User saying 'no' should return False."""
        tts = MagicMock()
        tts.speak = MagicMock(return_value=None)
        get_text = AsyncMock(return_value="no")
        confirmed = await default_guard.confirm(tts, get_text, "Are you sure?")
        assert confirmed is False

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self, default_guard: SafetyGuard) -> None:
        """A timeout during confirmation should return False."""
        tts = MagicMock()
        tts.speak = MagicMock(return_value=None)
        get_text = AsyncMock(side_effect=asyncio.TimeoutError)
        confirmed = await default_guard.confirm(tts, get_text, "Are you sure?")
        assert confirmed is False

    @pytest.mark.asyncio
    async def test_async_speak_handled(self, default_guard: SafetyGuard) -> None:
        """If tts.speak returns a coroutine it should be awaited."""
        tts = MagicMock()
        tts.speak = MagicMock(return_value=asyncio.sleep(0))
        get_text = AsyncMock(return_value="yes")
        confirmed = await default_guard.confirm(tts, get_text, "Are you sure?")
        assert confirmed is True
