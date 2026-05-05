"""Shared audio device ownership for VoiceUse.

VoiceUse has multiple audio-capable subsystems: push-to-talk recording,
wake-word detection, confirmation capture, TTS playback, and realtime voice
plugins.  Opening independent ``pyaudio.PyAudio()`` instances inside each
subsystem makes ownership unclear and can cause microphone contention on some
systems.  ``AudioDevice`` centralizes the process-level PyAudio instance and
keeps a small allocation ledger so future state-machine work can reason about
who currently owns input and output streams.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    import pyaudio
except ImportError:  # pragma: no cover
    pyaudio = None  # type: ignore[assignment]

logger = logging.getLogger("voiceuse.audio_device")


@dataclass(frozen=True)
class AudioStreamLease:
    """Metadata for a stream opened through the shared audio device.

    Attributes:
        owner: Subsystem name that requested the stream.
        direction: ``input`` or ``output``.
        sample_rate: Requested stream sample rate.
        channels: Requested channel count.
        frames_per_buffer: Requested PyAudio buffer size.
    """

    owner: str
    direction: str
    sample_rate: int
    channels: int
    frames_per_buffer: int


class AudioDevice:
    """Owns the shared PyAudio instance and stream bookkeeping.

    The class intentionally stays thin: it does not mix audio, resample, or
    enforce a full state machine yet.  It creates one PyAudio instance lazily,
    opens streams for named owners, and terminates the instance only after all
    streams are closed or the application shuts down.
    """

    def __init__(self, pyaudio_module: Optional[Any] = None) -> None:
        """Create an audio device facade.

        Args:
            pyaudio_module: Optional test double. Defaults to the imported
                ``pyaudio`` package.
        """
        self._pyaudio_module = pyaudio_module if pyaudio_module is not None else pyaudio
        self._pa: Optional[Any] = None
        self._lock = threading.RLock()
        self._leases: Dict[int, AudioStreamLease] = {}

    @property
    def is_available(self) -> bool:
        """Return whether PyAudio is installed and can be used."""
        return self._pyaudio_module is not None

    @property
    def pa_int16(self) -> int:
        """Return the PyAudio 16-bit integer sample format constant."""
        if self._pyaudio_module is None:
            return 0
        return self._pyaudio_module.paInt16

    def ensure_started(self) -> Any:
        """Return the shared PyAudio instance, creating it lazily."""
        if self._pyaudio_module is None:
            raise RuntimeError("pyaudio is not installed; audio device is unavailable.")
        with self._lock:
            if self._pa is None:
                self._pa = self._pyaudio_module.PyAudio()
                logger.info("Shared PyAudio device initialised.")
            return self._pa

    def open_input_stream(self, owner: str, **kwargs: Any) -> Any:
        """Open and track an input stream for a named subsystem."""
        kwargs["input"] = True
        return self._open_stream(owner=owner, direction="input", **kwargs)

    def open_output_stream(self, owner: str, **kwargs: Any) -> Any:
        """Open and track an output stream for a named subsystem."""
        kwargs["output"] = True
        return self._open_stream(owner=owner, direction="output", **kwargs)

    def close_stream(self, stream: Any) -> None:
        """Stop, close, and forget a stream opened through this device."""
        with self._lock:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
            self._leases.pop(id(stream), None)

    def stop(self) -> None:
        """Close all tracked streams and terminate the shared PyAudio instance."""
        with self._lock:
            for stream_id in list(self._leases):
                # The stream object itself is not stored to avoid prolonging
                # object lifetime unexpectedly; subsystems still close their
                # own streams.  The ledger is cleared here for shutdown sanity.
                self._leases.pop(stream_id, None)
            if self._pa is not None:
                try:
                    self._pa.terminate()
                except Exception as exc:
                    logger.debug("Error terminating shared PyAudio device: %s", exc)
                self._pa = None

    def active_leases(self) -> list[AudioStreamLease]:
        """Return a snapshot of currently tracked stream allocations."""
        with self._lock:
            return list(self._leases.values())

    def _open_stream(self, owner: str, direction: str, **kwargs: Any) -> Any:
        """Open a PyAudio stream and record its lease metadata."""
        pa = self.ensure_started()
        stream = pa.open(**kwargs)
        lease = AudioStreamLease(
            owner=owner,
            direction=direction,
            sample_rate=int(kwargs.get("rate", 0)),
            channels=int(kwargs.get("channels", 0)),
            frames_per_buffer=int(kwargs.get("frames_per_buffer", 0)),
        )
        with self._lock:
            self._leases[id(stream)] = lease
        logger.debug("Opened %s audio stream for %s: %s", direction, owner, lease)
        return stream
