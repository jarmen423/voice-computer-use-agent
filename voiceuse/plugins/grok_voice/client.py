"""xAI Realtime API WebSocket client for Grok Voice.

Manages the WebSocket connection to ``wss://api.x.ai/v1/realtime``,
session initialisation, event parsing, and bidirectional audio
streaming via asyncio queues.
"""

import asyncio
import base64
import json
import logging
import traceback
from typing import Any, Callable, Dict, List, Optional

import websockets

logger = logging.getLogger("voiceuse.grok_voice.client")

XAI_REALTIME_URL = "wss://api.x.ai/v1/realtime"


class XAIRealtimeClient:
    """Async WebSocket client for the xAI Grok Voice Realtime API.

    Usage:
        client = XAIRealtimeClient(api_key="xai-...", session_config={...})
        await client.connect()
        asyncio.create_task(client.receive_loop())
        # Push audio bytes (24 kHz PCM mono s16le) into send_queue
        await client.send_queue.put(b"...")
        # Read assistant audio from receive_queue
        chunk = await client.receive_queue.get()
    """

    def __init__(
        self,
        api_key: str,
        session_config: Dict[str, Any],
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.api_key = api_key
        self.session_config = session_config
        self.on_event = on_event

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected: bool = False
        self._session_id: Optional[str] = None

        # Audio I/O queues
        self.send_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.receive_queue: asyncio.Queue[bytes] = asyncio.Queue()

        # Event queues for specific xAI events
        self.function_call_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self.interruption_queue: asyncio.Queue[None] = asyncio.Queue()

        # Control
        self._stop_event: asyncio.Event = asyncio.Event()
        self._receive_task: Optional[asyncio.Task[None]] = None
        self._send_task: Optional[asyncio.Task[None]] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the WebSocket and send session configuration."""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        logger.info("Connecting to xAI Realtime API...")
        self._ws = await websockets.connect(
            XAI_REALTIME_URL,
            extra_headers=headers,
            ping_interval=20,
            ping_timeout=10,
        )
        self._connected = True
        logger.info("WebSocket connected.")

        # Start background loops
        self._stop_event.clear()
        self._receive_task = asyncio.create_task(self._receive_loop(), name="xai-receive")
        self._send_task = asyncio.create_task(self._send_loop(), name="xai-send")

        # Wait for session.created before sending session.update
        try:
            await asyncio.wait_for(self._wait_for_event("session.created"), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("Timed out waiting for session.created from xAI.")
            raise RuntimeError("xAI Realtime session handshake timeout")

        # Configure session
        await self._send_event("session.update", {"session": self.session_config})
        logger.info("Session update sent.")

    async def disconnect(self) -> None:
        """Close the WebSocket and cancel background tasks."""
        self._stop_event.set()
        for task in (self._receive_task, self._send_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as exc:
                logger.debug("WebSocket close error: %s", exc)
        self._connected = False
        logger.info("WebSocket disconnected.")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def commit_audio(self) -> None:
        """Signal that the user has stopped speaking (if not using server VAD)."""
        await self._send_event("input_audio_buffer.commit", {})

    async def cancel_response(self) -> None:
        """Cancel any in-progress assistant response (used on interruption)."""
        await self._send_event("response.cancel", {})

    async def create_response(self) -> None:
        """Request a new response from the model."""
        await self._send_event("response.create", {})

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------

    async def _receive_loop(self) -> None:
        """Read events from the WebSocket and dispatch them."""
        while not self._stop_event.is_set():
            try:
                if self._ws is None:
                    break
                raw = await asyncio.wait_for(self._ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except websockets.ConnectionClosed:
                logger.warning("xAI WebSocket closed.")
                break
            except Exception as exc:
                logger.error("WebSocket receive error: %s", exc)
                break

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Non-JSON message from xAI: %s", raw[:200])
                continue

            event_type = event.get("type", "")
            logger.debug("xAI event: %s", event_type)

            # Notify external observer
            if self.on_event:
                try:
                    self.on_event(event_type, event)
                except Exception:
                    logger.exception("on_event callback error")

            await self._handle_event(event_type, event)

    async def _handle_event(self, event_type: str, event: Dict[str, Any]) -> None:
        """Route xAI server events to internal queues and logic."""
        if event_type == "session.created":
            self._session_id = event.get("session", {}).get("id")
            logger.info("Session created: %s", self._session_id)

        elif event_type == "response.audio.delta":
            # Base64-encoded PCM chunk
            b64 = event.get("delta", "")
            if b64:
                try:
                    pcm = base64.b64decode(b64)
                    await self.receive_queue.put(pcm)
                except Exception as exc:
                    logger.warning("Failed to decode audio delta: %s", exc)

        elif event_type == "response.function_call_arguments.done":
            await self.function_call_queue.put(event)

        elif event_type == "input_audio_buffer.speech_started":
            # User started speaking — interrupt assistant playback
            await self.interruption_queue.put(None)

        elif event_type == "error":
            logger.error("xAI error event: %s", event.get("error", {}))

    async def _wait_for_event(self, event_type: str) -> Dict[str, Any]:
        """Block until a specific event type arrives (used for handshake)."""
        while True:
            if self._ws is None:
                raise RuntimeError("WebSocket not connected")
            raw = await self._ws.recv()
            event = json.loads(raw)
            if event.get("type") == event_type:
                return event
            await self._handle_event(event.get("type", ""), event)

    # ------------------------------------------------------------------
    # Send loop
    # ------------------------------------------------------------------

    async def _send_loop(self) -> None:
        """Read audio chunks from ``send_queue`` and forward to xAI."""
        while not self._stop_event.is_set():
            try:
                chunk = await asyncio.wait_for(self.send_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            if not chunk:
                continue

            b64 = base64.b64encode(chunk).decode("utf-8")
            await self._send_event("input_audio_buffer.append", {"audio": b64})

    async def _send_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Send a JSON event to the WebSocket."""
        if self._ws is None or not self._connected:
            logger.debug("Dropping event %s — not connected.", event_type)
            return
        payload = {"type": event_type, **data}
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as exc:
            logger.warning("Failed to send event %s: %s", event_type, exc)
