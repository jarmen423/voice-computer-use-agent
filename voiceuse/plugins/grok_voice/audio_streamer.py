"""24 kHz PCM audio streaming for the Grok Voice plugin.

Handles microphone capture at 24 kHz mono s16le, assistant playback via
PyAudio, and interruption logic (stop playback + clear queues when the
user starts speaking).
"""

import asyncio
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("voiceuse.grok_voice.audio_streamer")

# PyAudio is imported lazily so the module loads even when audio is unavailable.
try:
    import pyaudio
except ImportError:  # pragma: no cover
    pyaudio = None  # type: ignore[assignment]

SAMPLE_RATE: int = 24000
CHANNELS: int = 1
FORMAT: int = pyaudio.paInt16 if pyaudio else 0  # s16le
CHUNK_SIZE: int = 960  # 20 ms @ 24 kHz (480 samples * 2 bytes)


class GrokAudioStreamer:
    """Captures microphone audio at 24 kHz and plays assistant responses.

    Args:
        send_queue: asyncio.Queue to push captured PCM bytes into.
        receive_queue: asyncio.Queue to read assistant PCM bytes from.
        interruption_queue: asyncio.Queue that receives ``None`` when the
            user starts speaking (triggers playback cancellation).
    """

    def __init__(
        self,
        send_queue: asyncio.Queue[bytes],
        receive_queue: asyncio.Queue[bytes],
        interruption_queue: asyncio.Queue[None],
    ) -> None:
        self.send_queue = send_queue
        self.receive_queue = receive_queue
        self.interruption_queue = interruption_queue

        self._pa: Optional[Any] = None
        self._input_stream: Optional[Any] = None
        self._output_stream: Optional[Any] = None
        self._stop_event: asyncio.Event = asyncio.Event()

        self._capture_task: Optional[asyncio.Task[None]] = None
        self._playback_task: Optional[asyncio.Task[None]] = None
        self._interruption_task: Optional[asyncio.Task[None]] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open PyAudio streams and start capture / playback / interruption tasks."""
        if pyaudio is None:
            raise RuntimeError("pyaudio is not installed; cannot start audio streaming.")

        self._pa = pyaudio.PyAudio()
        self._stop_event.clear()

        # Open input (microphone) stream
        try:
            self._input_stream = self._pa.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
            logger.info("Microphone stream opened at %d Hz.", SAMPLE_RATE)
        except Exception as exc:
            logger.error("Failed to open microphone: %s", exc)
            raise

        # Open output (speaker) stream
        try:
            self._output_stream = self._pa.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                output=True,
                frames_per_buffer=CHUNK_SIZE,
            )
            logger.info("Speaker stream opened at %d Hz.", SAMPLE_RATE)
        except Exception as exc:
            logger.error("Failed to open speaker: %s", exc)
            raise

        self._capture_task = asyncio.create_task(self._capture_loop(), name="grok-capture")
        self._playback_task = asyncio.create_task(self._playback_loop(), name="grok-playback")
        self._interruption_task = asyncio.create_task(
            self._interruption_loop(), name="grok-interrupt"
        )

    async def stop(self) -> None:
        """Stop all tasks and close PyAudio streams."""
        self._stop_event.set()

        for task in (self._capture_task, self._playback_task, self._interruption_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        for stream in (self._input_stream, self._output_stream):
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass

        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass

        logger.info("Audio streamer stopped.")

    # ------------------------------------------------------------------
    # Capture loop
    # ------------------------------------------------------------------

    async def _capture_loop(self) -> None:
        """Read microphone PCM and push into ``send_queue``."""
        while not self._stop_event.is_set():
            try:
                # PyAudio read is blocking → run in thread pool
                chunk = await asyncio.to_thread(
                    self._input_stream.read, CHUNK_SIZE, False  # exception_on_overflow=False
                )
                if chunk:
                    await self.send_queue.put(chunk)
            except Exception as exc:
                logger.warning("Audio capture error: %s", exc)
                await asyncio.sleep(0.1)

    # ------------------------------------------------------------------
    # Playback loop
    # ------------------------------------------------------------------

    async def _playback_loop(self) -> None:
        """Read assistant PCM from ``receive_queue`` and write to speakers."""
        while not self._stop_event.is_set():
            try:
                chunk = await asyncio.wait_for(self.receive_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            try:
                await asyncio.to_thread(self._output_stream.write, chunk)
            except Exception as exc:
                logger.warning("Audio playback error: %s", exc)

    # ------------------------------------------------------------------
    # Interruption loop
    # ------------------------------------------------------------------

    async def _interruption_loop(self) -> None:
        """Watch ``interruption_queue`` and cancel playback on user speech."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self.interruption_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue

            logger.info("Interruption triggered — clearing playback queue.")
            # Drain receive queue so old assistant audio is discarded
            while not self.receive_queue.empty():
                try:
                    self.receive_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
