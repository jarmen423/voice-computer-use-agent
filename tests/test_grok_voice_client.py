"""Tests for xAI realtime client helper event shapes."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from voiceuse.plugins.grok_voice.client import XAIRealtimeClient


@pytest.mark.asyncio
async def test_send_function_call_output_uses_public_event_shape() -> None:
    """Tool output should be sent through the public helper, not plugin internals."""
    client = XAIRealtimeClient(api_key="xai-test", session_config={})
    client._send_event = AsyncMock()  # type: ignore[method-assign]

    await client.send_function_call_output("call-123", "opened")

    client._send_event.assert_awaited_once_with(
        "conversation.item.create",
        {
            "item": {
                "type": "function_call_output",
                "call_id": "call-123",
                "output": "opened",
            }
        },
    )
