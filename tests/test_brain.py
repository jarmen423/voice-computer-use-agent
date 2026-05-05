"""Unit tests for voiceuse.brain dispatch logic (mocked, no real API calls)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voiceuse.brain import Brain, LLMError, LLMResponse, _LLMClient
from voiceuse.config import Config
from voiceuse.models import CommandResult, ToolCall, WindowInfo
from voiceuse.safety import SafetyGuard


@pytest.fixture
def mock_os_controller() -> MagicMock:
    """Return a mock OSController with all required methods."""
    ctrl = MagicMock()
    ctrl.find_window.return_value = None
    ctrl.focus_window.return_value = CommandResult(success=True, message="focused")
    ctrl.open_app.return_value = CommandResult(success=True, message="opened")
    ctrl.type_text.return_value = None
    ctrl.press_key.return_value = None
    ctrl.browser_search.return_value = CommandResult(success=True, message="searched")
    ctrl.split_view_apps.return_value = CommandResult(success=True, message="tiled")
    ctrl.execute_system.return_value = CommandResult(success=True, message="executed")
    ctrl.find_chat.return_value = CommandResult(success=True, message="found chat")
    ctrl.list_windows.return_value = [
        WindowInfo(title="Chrome", pid=1, rect=(0, 0, 100, 100), monitor_index=1)
    ]
    return ctrl


@pytest.fixture
def mock_vision_bridge() -> MagicMock:
    """Return a mock VisionBridge."""
    bridge = MagicMock()
    bridge.find_and_click = AsyncMock(return_value=CommandResult(success=True, message="clicked"))
    return bridge


@pytest.fixture
def mock_tts_manager() -> MagicMock:
    """Return a mock TTSManager."""
    tts = MagicMock()
    tts.speak = AsyncMock(return_value=None)
    return tts


@pytest.fixture
def mock_safety_guard() -> MagicMock:
    """Return a mock SafetyGuard that allows everything."""
    guard = MagicMock(spec=SafetyGuard)
    guard.check_command.return_value = MagicMock(
        is_safe=True,
        requires_confirmation=False,
        is_allowed=True,
        denial_reason="",
    )
    guard.confirm = AsyncMock(return_value=True)
    return guard


@pytest.fixture
def brain(
    mock_os_controller: MagicMock,
    mock_vision_bridge: MagicMock,
    mock_tts_manager: MagicMock,
    mock_safety_guard: MagicMock,
) -> Brain:
    """Return a Brain wired with mock subsystems."""
    config = Config()
    config.safety.audit_enabled = False
    return Brain(
        config=config,
        safety=mock_safety_guard,
        os_controller=mock_os_controller,
        vision_bridge=mock_vision_bridge,
        tts_manager=mock_tts_manager,
        get_confirmation_text=AsyncMock(return_value="yes"),
    )


class TestProcessCommand:
    """Tests for Brain.process_command end-to-end flow."""

    @pytest.mark.asyncio
    async def test_conversational_response_when_no_tools(
        self, brain: Brain
    ) -> None:
        """If the LLM returns no tool calls, the content is spoken."""
        with patch.object(
            brain.llm,
            "chat",
            AsyncMock(return_value=LLMResponse(content="Hello there", tool_calls=[])),
        ):
            result = await brain.process_command("hi")
        assert result.success is True
        assert "Hello there" in result.message

    @pytest.mark.asyncio
    async def test_dispatch_open_app(self, brain: Brain, mock_os_controller: MagicMock) -> None:
        """Brain should dispatch open_app to OSController."""
        with patch.object(
            brain.llm,
            "chat",
            AsyncMock(
                side_effect=[
                    LLMResponse(
                        tool_calls=[ToolCall(tool_name="open_app", parameters={"app_name": "chrome"})]
                    ),
                    LLMResponse(content="Chrome is open.", tool_calls=[]),
                ]
            ),
        ):
            result = await brain.process_command("open chrome")
        assert result.success is True
        mock_os_controller.open_app.assert_called_once_with(app_name="chrome")

    @pytest.mark.asyncio
    async def test_process_command_includes_prior_turn_history(
        self, brain: Brain, mock_os_controller: MagicMock
    ) -> None:
        """Follow-up voice turns should include prior tool results in LLM context."""
        chat = AsyncMock(
            side_effect=[
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            tool_name="open_app",
                            parameters={"app_name": "chrome"},
                            call_id="call_open_chrome",
                        )
                    ]
                ),
                LLMResponse(content="Chrome is open.", tool_calls=[]),
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            tool_name="type_text",
                            parameters={"text": "hello", "app_name": "chrome"},
                            call_id="call_type_hello",
                        )
                    ]
                ),
                LLMResponse(content="Typed hello.", tool_calls=[]),
            ]
        )
        with patch.object(brain.llm, "chat", chat):
            await brain.process_command("open chrome")
            await brain.process_command("type hello in the search bar")

        second_messages = chat.await_args_list[2].kwargs["messages"]
        assert any(msg.get("role") == "tool" for msg in second_messages)
        assert any(
            msg.get("role") == "assistant" and "Result: opened" in msg.get("content", "")
            for msg in second_messages
        )

    @pytest.mark.asyncio
    async def test_desktop_context_uses_short_ttl_cache(
        self, brain: Brain, mock_os_controller: MagicMock
    ) -> None:
        """Repeated context builds should not re-enumerate windows inside the TTL."""
        first = await brain._build_desktop_context()
        second = await brain._build_desktop_context()

        assert first == second
        assert "Chrome" in first
        assert mock_os_controller.list_windows.call_count == 1

    @pytest.mark.asyncio
    async def test_dispatch_click_element(
        self, brain: Brain, mock_vision_bridge: MagicMock
    ) -> None:
        """Brain should dispatch click_element to VisionBridge."""
        with patch.object(
            brain.llm,
            "chat",
            AsyncMock(
                side_effect=[
                    LLMResponse(
                        tool_calls=[
                            ToolCall(
                                tool_name="click_element",
                                parameters={"description": "submit button"},
                            )
                        ]
                    ),
                    LLMResponse(content="Clicked.", tool_calls=[]),
                ]
            ),
        ):
            result = await brain.process_command("click the submit button")
        assert result.success is True
        mock_vision_bridge.find_and_click.assert_awaited_once_with(
            description="submit button", app_name=None
        )

    @pytest.mark.asyncio
    async def test_llm_error_handling(self, brain: Brain) -> None:
        """If the LLM raises LLMError, Brain should return a graceful failure."""
        with patch.object(brain.llm, "chat", AsyncMock(side_effect=LLMError("down"))):
            result = await brain.process_command("do something")
        assert result.success is False
        assert "couldn't reach" in result.message.lower()

    @pytest.mark.asyncio
    async def test_safety_blocks_unconfirmed(
        self,
        brain: Brain,
        mock_safety_guard: MagicMock,
    ) -> None:
        """If safety blocks a call and user declines, action is cancelled."""
        mock_safety_guard.check_command.return_value = MagicMock(
            is_safe=False, requires_confirmation=True, confirmation_prompt="Sure?"
        )
        mock_safety_guard.confirm = AsyncMock(return_value=False)
        with patch.object(
            brain.llm,
            "chat",
            AsyncMock(
                return_value=LLMResponse(
                    tool_calls=[ToolCall(tool_name="execute_system", parameters={"command": "echo hi"})]
                )
            ),
        ):
            result = await brain.process_command("run a command")
        assert result.success is False
        assert "cancelled" in result.message.lower()


class TestLLMClient:
    """Tests for the internal _LLMClient wrapper."""

    @pytest.mark.asyncio
    async def test_raises_when_no_providers(self) -> None:
        """_LLMClient.chat should raise LLMError when no clients are configured."""
        config = Config()
        config.llm.api_key = None
        config.llm.fallback_api_key = None
        config.llm.cerebras_api_key = None
        client = _LLMClient(config.llm)
        with pytest.raises(LLMError):
            await client.chat(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_cerebras_provider_used_when_configured(self) -> None:
        """When provider is 'cerebras', _LLMClient should use the Cerebras client."""
        config = Config()
        config.llm.provider = "cerebras"
        config.llm.model = "llama3.1-70b"
        config.llm.cerebras_api_key = "test-csk"
        client = _LLMClient(config.llm)
        assert client._has_cerebras is True
        with patch.object(
            client._cerebras_client.chat.completions,
            "create",
            AsyncMock(return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="hi", tool_calls=None))], model="llama3.1-70b")),
        ):
            resp = await client.chat(messages=[{"role": "user", "content": "hello"}])
        assert resp.content == "hi"

    @pytest.mark.asyncio
    async def test_provider_retries_transient_errors_before_fallback(self) -> None:
        """Transient provider errors should be retried before the fallback path runs."""
        config = Config()
        config.llm.provider = "openai"
        config.llm.model = "gpt-test"
        config.llm.fallback_provider = None

        response = MagicMock(
            choices=[MagicMock(message=MagicMock(content="recovered", tool_calls=None))],
            model="gpt-test",
        )
        create = AsyncMock(side_effect=[ConnectionError("temporary"), response])
        client = _LLMClient(config.llm)
        client._openai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        result = await client.chat(messages=[{"role": "user", "content": "hello"}])

        assert result.content == "recovered"
        assert create.await_count == 2
