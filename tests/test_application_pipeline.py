"""Integration-style tests for the Application voice command pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from voiceuse.config import Config
from voiceuse.main import Application, ApplicationState, ApplicationStateMachine
from voiceuse.models import CommandResult


def _mark_idle(app: Application) -> None:
    """Move a unit-test application through the normal startup states."""
    app._set_state(ApplicationState.INITIALISING)
    app._set_state(ApplicationState.IDLE)


@pytest.mark.asyncio
async def test_pipeline_runs_stt_brain_and_interrupting_tts() -> None:
    """A normal voice turn should flow STT -> Brain -> interrupting TTS."""
    app = Application(Config())
    _mark_idle(app)
    app.input_manager = AsyncMock()
    app.input_manager.transcribe_audio = AsyncMock(return_value="open chrome")
    app.brain = AsyncMock()
    app.brain.process_command = AsyncMock(
        return_value=CommandResult(success=True, message="Opened Chrome.")
    )
    app.command_backend = app.brain
    app.tts_manager = AsyncMock()
    app.tts_manager.speak = AsyncMock(return_value=None)

    await app.pipeline(b"audio")

    app.input_manager.transcribe_audio.assert_awaited_once_with(b"audio")
    app.brain.process_command.assert_awaited_once_with("open chrome")
    app.tts_manager.speak.assert_awaited_once_with("Opened Chrome.", interrupt=True)
    assert app.state == ApplicationState.IDLE


@pytest.mark.asyncio
async def test_pipeline_can_use_external_agent_backend() -> None:
    """The voice pipeline should allow STT -> external agent -> TTS routing."""
    app = Application(Config())
    _mark_idle(app)
    app.input_manager = AsyncMock()
    app.input_manager.transcribe_audio = AsyncMock(return_value="search for pirates")
    app.command_backend = AsyncMock()
    app.command_backend.process_command = AsyncMock(
        return_value=CommandResult(success=True, message="Searched for pirates.")
    )
    app.tts_manager = AsyncMock()
    app.tts_manager.speak = AsyncMock(return_value=None)

    await app.pipeline(b"audio")

    app.command_backend.process_command.assert_awaited_once_with("search for pirates")
    app.tts_manager.speak.assert_awaited_once_with("Searched for pirates.", interrupt=True)
    assert app.state == ApplicationState.IDLE


@pytest.mark.asyncio
async def test_pipeline_handles_empty_transcription_without_brain() -> None:
    """Empty STT output should short-circuit before LLM/tool execution."""
    app = Application(Config())
    _mark_idle(app)
    app.input_manager = AsyncMock()
    app.input_manager.transcribe_audio = AsyncMock(return_value="")
    app.command_backend = AsyncMock()
    app.tts_manager = AsyncMock()
    app.tts_manager.speak = AsyncMock(return_value=None)

    await app.pipeline(b"audio")

    app.command_backend.process_command.assert_not_called()
    app.tts_manager.speak.assert_awaited_once_with("I didn't hear anything.", interrupt=True)
    assert app.state == ApplicationState.IDLE


@pytest.mark.asyncio
async def test_hotkey_press_cancels_current_tts() -> None:
    """Starting a new voice turn should stop stale assistant speech."""
    app = Application(Config())
    _mark_idle(app)
    app.tts_manager = AsyncMock()
    app.tts_manager.cancel = AsyncMock(return_value=None)

    await app.on_hotkey_press()

    app.tts_manager.cancel.assert_awaited_once()
    assert app.state == ApplicationState.LISTENING


def test_application_state_machine_blocks_invalid_transition() -> None:
    """Stopped applications should not move back into runtime states."""
    machine = ApplicationStateMachine()
    machine.transition(ApplicationState.INITIALISING)
    machine.transition(ApplicationState.IDLE)
    machine.transition(ApplicationState.SHUTTING_DOWN)
    machine.transition(ApplicationState.STOPPED)

    with pytest.raises(RuntimeError):
        machine.transition(ApplicationState.LISTENING)
