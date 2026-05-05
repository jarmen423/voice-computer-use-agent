"""Grok Voice plugin for VoiceUse.

Implements a replacement pipeline using the xAI Grok Voice Realtime API.
When enabled, this plugin bypasses the default Brain + InputManager STT +
TTSManager chain and streams 24 kHz PCM audio directly to xAI over a
WebSocket.  Function calls from the model are dispatched to the existing
:class:`OSController` and :class:`VisionBridge`.
"""

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from voiceuse.action_audit import ActionAuditLog
from voiceuse.config import Config
from voiceuse.os_controller import OSController
from voiceuse.vision_bridge import VisionBridge
from voiceuse.safety import SafetyGuard
from voiceuse.plugins.base import PluginBase
from voiceuse.plugins.grok_voice.client import XAIRealtimeClient
from voiceuse.plugins.grok_voice.audio_streamer import GrokAudioStreamer
from voiceuse.models import CommandResult, ToolCall
from voiceuse.tool_registry import TOOL_SCHEMAS, dispatch_tool_call
from voiceuse.audio_device import AudioDevice

logger = logging.getLogger("voiceuse.grok_voice.plugin")


class GrokVoicePlugin(PluginBase):
    """VoiceUse plugin that streams audio to/from the xAI Grok Voice Realtime API.

    Replaces the default STT → LLM → TTS pipeline with a single WebSocket
    that handles speech-to-text, reasoning, and text-to-speech end-to-end.
    """

    name: str = "grok_voice"

    def __init__(self) -> None:
        self.config: Optional[Config] = None
        self.os_controller: Optional[OSController] = None
        self.vision_bridge: Optional[VisionBridge] = None
        self.safety_guard: Optional[SafetyGuard] = None
        self.tts_manager: Optional[Any] = None
        self.get_confirmation_text: Optional[Callable[[], Awaitable[str]]] = None
        self.audio_device: Optional[AudioDevice] = None
        self.audit_log: Optional[ActionAuditLog] = None

        self._client: Optional[XAIRealtimeClient] = None
        self._streamer: Optional[GrokAudioStreamer] = None
        self._active: bool = False
        self._stop_event: asyncio.Event = asyncio.Event()

        self._function_task: Optional[asyncio.Task[None]] = None
        self._interruption_task: Optional[asyncio.Task[None]] = None

    # ------------------------------------------------------------------
    # PluginBase lifecycle
    # ------------------------------------------------------------------

    async def on_enable(
        self,
        config: Config,
        os_controller: OSController,
        vision_bridge: VisionBridge,
        safety_guard: SafetyGuard,
        tts_manager: Any,
        get_confirmation_text: Callable[[], Awaitable[str]],
        audio_device: AudioDevice,
    ) -> None:
        """Store subsystem references and connect to xAI Realtime."""
        self.config = config
        self.os_controller = os_controller
        self.vision_bridge = vision_bridge
        self.safety_guard = safety_guard
        self.tts_manager = tts_manager
        self.get_confirmation_text = get_confirmation_text
        self.audio_device = audio_device
        self.audit_log = ActionAuditLog(config)

        gcfg = config.plugins.grok_voice
        if not gcfg.api_key:
            raise RuntimeError("XAI_API_KEY is required for Grok Voice plugin.")

        session_config: Dict[str, Any] = {
            "modalities": ["audio", "text"],
            "instructions": gcfg.instructions,
            "voice": gcfg.voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": gcfg.input_audio_transcription_model,
            },
            "turn_detection": {
                "type": gcfg.turn_detection_type,
            },
            "tools": TOOL_SCHEMAS,
            "tool_choice": "auto",
        }

        self._client = XAIRealtimeClient(
            api_key=gcfg.api_key,
            session_config=session_config,
        )
        await self._client.connect()

        self._streamer = GrokAudioStreamer(
            send_queue=self._client.send_queue,
            receive_queue=self._client.receive_queue,
            interruption_queue=self._client.interruption_queue,
            audio_device=self.audio_device,
        )
        await self._streamer.start()

        self._active = True
        logger.info("GrokVoicePlugin enabled.")

    async def on_disable(self) -> None:
        """Disconnect from xAI and release audio resources."""
        self._active = False
        self._stop_event.set()

        for task in (self._function_task, self._interruption_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._streamer is not None:
            await self._streamer.stop()

        if self._client is not None:
            await self._client.disconnect()

        logger.info("GrokVoicePlugin disabled.")

    async def run(self) -> None:
        """Main task — consumes function calls and interruptions from xAI."""
        self._stop_event.clear()
        self._function_task = asyncio.create_task(
            self._function_call_loop(), name="grok-function-dispatch"
        )
        self._interruption_task = asyncio.create_task(
            self._interruption_loop(), name="grok-interrupt-dispatch"
        )

        # Keep running until shutdown
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            pass

    def is_active(self) -> bool:
        return self._active

    # ------------------------------------------------------------------
    # Hotkey hooks
    # ------------------------------------------------------------------

    async def on_hotkey_press(self) -> None:
        """Hotkey pressed — streaming is already continuous, nothing extra needed."""
        logger.debug("GrokVoicePlugin received hotkey press.")

    async def on_hotkey_release(self, audio_bytes: bytes) -> None:
        """Hotkey released — if not using server VAD, commit the audio buffer."""
        if self._client is not None:
            gcfg = self.config.plugins.grok_voice if self.config else None
            if gcfg and gcfg.turn_detection_type != "server_vad":
                await self._client.commit_audio()
                await self._client.create_response()

    async def on_wake_word(self, audio_bytes: bytes) -> None:
        """Wake word detected — same as hotkey release for non-server-VAD mode."""
        await self.on_hotkey_release(audio_bytes)

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    async def _function_call_loop(self) -> None:
        """Wait for xAI function_call events and dispatch them locally."""
        if self._client is None:
            return
        while not self._stop_event.is_set():
            try:
                event = await asyncio.wait_for(
                    self._client.function_call_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue

            try:
                await self._dispatch_function_call(event)
            except Exception as exc:
                logger.exception("Function call dispatch error: %s", exc)

    async def _interruption_loop(self) -> None:
        """Watch for user-speech interruptions and cancel in-flight responses."""
        if self._client is None:
            return
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._client.interruption_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue

            logger.info("User speech detected — cancelling assistant response.")
            try:
                await self._client.cancel_response()
            except Exception as exc:
                logger.warning("Failed to cancel response: %s", exc)

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    async def _dispatch_function_call(self, event: Dict[str, Any]) -> None:
        """Parse an xAI ``response.function_call_arguments.done`` event and
        route it to :class:`OSController` or :class:`VisionBridge`.
        """
        call_id = event.get("call_id", "unknown")
        name = event.get("name", "")
        arguments_raw = event.get("arguments", "{}")
        logger.info("Function call: %s (%s)", name, call_id)

        try:
            params = json.loads(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw
        except json.JSONDecodeError as exc:
            logger.error("Invalid function arguments JSON: %s", exc)
            return

        # Safety check before execution
        if self.safety_guard is not None:
            from voiceuse.models import ToolCall
            tc = ToolCall(tool_name=name, parameters=params)
            safety_result = self.safety_guard.check_command(tc)
            if not safety_result.is_allowed:
                if self.audit_log is not None:
                    await self.audit_log.record(
                        source="grok_voice",
                        tool_call=tc,
                        decision="denied",
                        reason=safety_result.denial_reason,
                    )
                logger.warning("Safety policy denied function call %s: %s", name, safety_result.denial_reason)
                return
            if not safety_result.is_safe:
                if self.tts_manager is None or self.get_confirmation_text is None:
                    logger.warning("Cannot confirm %s because confirmation services are unavailable.", name)
                    return
                confirmed = await self.safety_guard.confirm(
                    tts_manager=self.tts_manager,
                    get_confirmation_text=self.get_confirmation_text,
                    confirmation_prompt=safety_result.confirmation_prompt,
                )
                if not confirmed:
                    if self.audit_log is not None:
                        await self.audit_log.record(
                            source="grok_voice",
                            tool_call=tc,
                            decision="denied",
                            reason="User declined confirmation.",
                        )
                    logger.info("User declined function call %s", name)
                    return
                if self.audit_log is not None:
                    await self.audit_log.record(
                        source="grok_voice",
                        tool_call=tc,
                        decision="confirmed",
                        reason=safety_result.confirmation_prompt,
                    )
            elif self.audit_log is not None:
                await self.audit_log.record(
                    source="grok_voice",
                    tool_call=tc,
                    decision="allowed",
                )

        result_message = ""
        try:
            result_message = await self._execute_tool(name, params)
            if self.audit_log is not None:
                await self.audit_log.record(
                    source="grok_voice",
                    tool_call=ToolCall(tool_name=name, parameters=params),
                    decision="executed",
                    result=CommandResult(success=True, message=result_message),
                )
        except Exception as exc:
            logger.exception("Tool execution failed for %s", name)
            result_message = f"Error executing {name}: {exc}"
            if self.audit_log is not None:
                await self.audit_log.record(
                    source="grok_voice",
                    tool_call=ToolCall(tool_name=name, parameters=params),
                    decision="failed",
                    result=CommandResult(success=False, message=str(exc)),
                    reason=str(exc),
                )

        # Send function output back to xAI so the model can continue
        if self._client is not None:
            await self._client._send_event(
                "conversation.item.create",
                {
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result_message,
                    }
                },
            )
            await self._client.create_response()

    async def _execute_tool(self, name: str, params: Dict[str, Any]) -> str:
        """Execute a single tool through the shared VoiceUse tool registry."""
        if self.os_controller is None or self.vision_bridge is None:
            raise RuntimeError("Subsystems not available for tool execution.")

        result = await dispatch_tool_call(
            tool_call=ToolCall(tool_name=name, parameters=params),
            os_controller=self.os_controller,
            vision_bridge=self.vision_bridge,
        )
        return result.message
