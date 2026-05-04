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

from voiceuse.config import Config
from voiceuse.os_controller import OSController
from voiceuse.vision_bridge import VisionBridge
from voiceuse.safety import SafetyGuard
from voiceuse.plugins.base import PluginBase
from voiceuse.plugins.grok_voice.client import XAIRealtimeClient
from voiceuse.plugins.grok_voice.audio_streamer import GrokAudioStreamer

logger = logging.getLogger("voiceuse.grok_voice.plugin")

# Tool schemas exposed to xAI Realtime session (OpenAI-compatible)
_TOOL_SCHEMAS: list[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Launch an application or bring it to foreground if already running.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Name of the application, e.g. 'Codex', 'Chrome'."}
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": "Find a window by title/substring and bring it to foreground.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Window title substring or application name."}
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": "Use computer vision to find an element described by the user and click it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Natural-language description of the element to click."},
                    "app_name": {"type": "string", "description": "Optional app/window context to narrow the search."},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into the currently focused window or a specified app's text input.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type."},
                    "app_name": {"type": "string", "description": "Optional target app name."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_search",
            "description": "Open browser, focus address bar, type query/URL, submit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "browser": {"type": "string", "description": "Browser name (optional, uses default if omitted)."},
                    "query": {"type": "string", "description": "Search query or URL to type."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_system",
            "description": "Execute a safe system command. Dangerous commands are blocked by an allow-list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "System command to execute."}
                },
                "required": ["command"],
            },
        },
    },
]


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
    ) -> None:
        """Store subsystem references and connect to xAI Realtime."""
        self.config = config
        self.os_controller = os_controller
        self.vision_bridge = vision_bridge
        self.safety_guard = safety_guard
        self.tts_manager = tts_manager
        self.get_confirmation_text = get_confirmation_text

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
            "tools": _TOOL_SCHEMAS,
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
            if not safety_result.is_safe:
                confirmed = await self.safety_guard.confirm(
                    tts_manager=self.tts_manager,
                    get_confirmation_text=self.get_confirmation_text,
                    confirmation_prompt=safety_result.confirmation_prompt,
                )
                if not confirmed:
                    logger.info("User declined function call %s", name)
                    return

        result_message = ""
        try:
            result_message = await self._execute_tool(name, params)
        except Exception as exc:
            logger.exception("Tool execution failed for %s", name)
            result_message = f"Error executing {name}: {exc}"

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
        """Execute a single tool against OSController or VisionBridge."""
        if self.os_controller is None or self.vision_bridge is None:
            raise RuntimeError("Subsystems not available for tool execution.")

        # OSController tools
        if name in {"open_app", "focus_window", "type_text", "browser_search", "execute_system", "find_chat"}:
            if name == "focus_window":
                window = self.os_controller.find_window(params.get("app_name", ""))
                if window is None:
                    return f"No window found matching '{params.get('app_name')}'"
                result = self.os_controller.focus_window(window)
                return result.message if hasattr(result, "message") else str(result)

            if name == "type_text":
                text = params.get("text", "")
                app_name = params.get("app_name")
                if app_name:
                    window = self.os_controller.find_window(app_name)
                    if window is not None:
                        focus_res = self.os_controller.focus_window(window)
                        if hasattr(focus_res, "success") and not focus_res.success:
                            return f"Failed to focus {app_name}: {focus_res.message}"
                result = self.os_controller.type_text(text)
                return f"Typed text into {'app ' + app_name if app_name else 'current focus'}."

            method = getattr(self.os_controller, name, None)
            if method is None:
                return f"Unknown tool: {name}"
            if asyncio.iscoroutinefunction(method):
                result = await method(**params)
            else:
                result = method(**params)
            return result.message if hasattr(result, "message") else str(result)

        # VisionBridge tools
        if name == "click_element":
            result = await self.vision_bridge.find_and_click(
                description=params.get("description", ""),
                app_name=params.get("app_name"),
            )
            return result.message if hasattr(result, "message") else str(result)

        return f"Tool '{name}' is not implemented."
