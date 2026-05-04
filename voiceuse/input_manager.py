"""Input Manager for VoiceUse: hotkeys, wake word, VAD, and streaming STT."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import struct
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any, Callable, Coroutine, List, Optional

try:
    import pyaudio
except ImportError:  # pragma: no cover
    pyaudio = None  # type: ignore[assignment]

try:
    import webrtcvad
except ImportError:  # pragma: no cover
    webrtcvad = None  # type: ignore[assignment]

try:
    from groq import Groq
except ImportError:  # pragma: no cover
    Groq = None  # type: ignore[assignment,misc]

try:
    import pvporcupine
except ImportError:  # pragma: no cover
    pvporcupine = None  # type: ignore[assignment]

try:
    from pynput import keyboard
except ImportError:  # pragma: no cover
    keyboard = None  # type: ignore[assignment]

from voiceuse.config import Config

logger = logging.getLogger("voiceuse.input_manager")


Callback = Callable[[], Coroutine[Any, Any, None]]
WakeWordCallback = Callable[[bytes], Coroutine[Any, Any, None]]


class InputManager:
    """Manages audio input: hotkeys, wake word, VAD, and streaming STT to Groq."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.audio_config = config.audio
        self.stt_config = config.stt

        # State
        self._is_recording: bool = False
        self._audio_buffer: List[bytes] = []

        # Callbacks (set by register_callbacks)
        self._on_hotkey_start: Optional[Callback] = None
        self._on_hotkey_stop: Optional[WakeWordCallback] = None
        self._on_wake_word: Optional[WakeWordCallback] = None

        # PyAudio
        self._pa: Optional[Any] = None
        self._stream: Optional[Any] = None
        self._sample_rate: int = self.audio_config.sample_rate
        self._channels: int = 1
        self._format: int = pyaudio.paInt16 if pyaudio else 0
        self._chunk_size: int = int(
            self._sample_rate * self.audio_config.chunk_duration_ms / 1000
        )

        # Groq client
        self._groq_client: Optional[Any] = None
        if Groq is not None and self.stt_config.api_key:
            self._groq_client = Groq(api_key=self.stt_config.api_key)

        # Hotkey listener
        self._hotkey_listener: Optional[keyboard.Listener] = None
        self._hotkey_thread: Optional[threading.Thread] = None
        self._stop_hotkey: bool = False

        # Wake word
        self._porcupine: Optional[Any] = None
        self._wake_word_thread: Optional[threading.Thread] = None
        self._stop_wake_word: bool = False

        # Async loop reference (for thread → async callbacks)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def register_callbacks(
        self,
        on_hotkey_start: Optional[Callback] = None,
        on_hotkey_stop: Optional[WakeWordCallback] = None,
        on_wake_word: Optional[WakeWordCallback] = None,
    ) -> None:
        """Register async callbacks invoked from background threads."""
        self._on_hotkey_start = on_hotkey_start
        self._on_hotkey_stop = on_hotkey_stop
        self._on_wake_word = on_wake_word

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start hotkey and (optionally) wake-word listeners."""
        self._loop = asyncio.get_running_loop()

        if pyaudio is None:
            logger.error("pyaudio is not installed; audio input is disabled.")
            print("[InputManager] ERROR: pyaudio not installed — audio input disabled.")
            return

        self._pa = pyaudio.PyAudio()

        # Start hotkey listener in a background thread
        self._stop_hotkey = False
        self._hotkey_thread = threading.Thread(
            target=self._hotkey_worker, daemon=True, name="hotkey-listener"
        )
        self._hotkey_thread.start()
        print(f"[InputManager] Hotkey listener started ({self.audio_config.hotkey}).")

        # Start wake-word listener if configured
        if self.audio_config.wake_word:
            self._stop_wake_word = False
            self._wake_word_thread = threading.Thread(
                target=self._wake_word_worker, daemon=True, name="wake-word-listener"
            )
            self._wake_word_thread.start()
            print(f"[InputManager] Wake-word listener started ('{self.audio_config.wake_word}').")
        else:
            print("[InputManager] Wake-word disabled in config.")

    async def stop(self) -> None:
        """Stop all listeners and release audio resources."""
        logger.info("InputManager stopping...")
        self._stop_hotkey = True
        self._stop_wake_word = True

        # Stop pynput listener
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception as exc:  # pragma: no cover
                logger.debug("Error stopping hotkey listener: %s", exc)
            self._hotkey_listener = None

        # Close PyAudio stream
        self._close_stream()

        # Terminate Porcupine
        if self._porcupine is not None:
            try:
                self._porcupine.delete()
            except Exception as exc:  # pragma: no cover
                logger.debug("Error deleting porcupine: %s", exc)
            self._porcupine = None

        # Terminate PyAudio
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception as exc:  # pragma: no cover
                logger.debug("Error terminating pyaudio: %s", exc)
            self._pa = None

        # Join threads (with timeout so we don't hang)
        if self._hotkey_thread is not None and self._hotkey_thread.is_alive():
            self._hotkey_thread.join(timeout=2.0)
        if self._wake_word_thread is not None and self._wake_word_thread.is_alive():
            self._wake_word_thread.join(timeout=2.0)

        print("[InputManager] Stopped.")

    # ------------------------------------------------------------------
    # Hotkey handling
    # ------------------------------------------------------------------

    def _hotkey_worker(self) -> None:
        """Thread worker that runs the pynput keyboard listener."""
        if keyboard is None:
            logger.warning("pynput is not installed; hotkey support disabled.")
            return

        target_key = self.audio_config.hotkey.strip().lower()

        def on_press(key: keyboard.Key) -> bool:
            if self._stop_hotkey:
                return False
            if not self._match_hotkey(key, target_key):
                return True
            if not self._is_recording:
                self._is_recording = True
                self._audio_buffer.clear()
                self._open_stream()
                self._schedule_async(self._on_hotkey_start)
            return True

        def on_release(key: keyboard.Key) -> bool:
            if self._stop_hotkey:
                return False
            if not self._match_hotkey(key, target_key):
                return True
            if self._is_recording:
                self._is_recording = False
                audio_bytes = self._stop_and_collect()
                self._schedule_async(self._on_hotkey_stop, audio_bytes)
            return True

        try:
            self._hotkey_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._hotkey_listener.start()
            self._hotkey_listener.join()
        except Exception as exc:
            logger.error("Hotkey listener crashed: %s", exc)

    @staticmethod
    def _match_hotkey(key: Any, target: str) -> bool:
        """Check if a pynput key matches the configured hotkey string."""
        try:
            if target == "right ctrl" and key == keyboard.Key.ctrl_r:
                return True
            if target == "left ctrl" and key == keyboard.Key.ctrl_l:
                return True
            if target == "ctrl" and key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                return True
            if target == "right shift" and key == keyboard.Key.shift_r:
                return True
            if target == "left shift" and key == keyboard.Key.shift_l:
                return True
            if target == "shift" and key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
                return True
            if target == "right alt" and key == keyboard.Key.alt_r:
                return True
            if target == "left alt" and key == keyboard.Key.alt_l:
                return True
            if target == "alt" and key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r):
                return True
            if hasattr(key, "char") and key.char is not None:
                return key.char.lower() == target
            if hasattr(key, "name") and key.name is not None:
                return key.name.lower() == target.replace(" ", "_")
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # Wake-word handling
    # ------------------------------------------------------------------

    def _wake_word_worker(self) -> None:
        """Thread worker that listens for the wake word via Porcupine."""
        if pvporcupine is None:
            logger.warning("pvporcupine is not installed; wake-word support disabled.")
            return

        try:
            access_key = os.environ.get("PORCUPINE_ACCESS_KEY", "")
            keyword_path = self.audio_config.wake_word_model_path

            if keyword_path and Path(keyword_path).exists():
                self._porcupine = pvporcupine.create(
                    access_key=access_key,
                    keyword_paths=[str(keyword_path)],
                )
            else:
                # Use built-in keyword for "computer" if available
                try:
                    self._porcupine = pvporcupine.create(
                        access_key=access_key,
                        keywords=["computer"],
                    )
                except Exception as exc:
                    logger.warning("Built-in 'computer' keyword failed (%s); trying 'hey computer'.", exc)
                    try:
                        self._porcupine = pvporcupine.create(
                            access_key=access_key,
                            keywords=["hey computer"],
                        )
                    except Exception as exc2:
                        logger.error("Could not create Porcupine instance: %s", exc2)
                        return
        except Exception as exc:
            logger.error("Failed to initialise wake-word engine: %s", exc)
            return

        porcupine_sample_rate = self._porcupine.sample_rate
        frame_length = self._porcupine.frame_length
        pa = pyaudio.PyAudio()
        stream: Optional[Any] = None

        try:
            stream = pa.open(
                rate=porcupine_sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=frame_length,
            )
            print(f"[InputManager] Porcupine listening (sample_rate={porcupine_sample_rate}).")
        except Exception as exc:
            logger.error("Failed to open microphone for wake-word: %s", exc)
            pa.terminate()
            return

        try:
            while not self._stop_wake_word:
                pcm = stream.read(frame_length, exception_on_overflow=False)
                if not pcm:
                    continue
                pcm_unpacked = struct.unpack_from("h" * frame_length, pcm)
                keyword_index = self._porcupine.process(pcm_unpacked)
                if keyword_index >= 0:
                    print("[InputManager] Wake word detected!")
                    self._schedule_async(self._trigger_wake_word_recording)
        except Exception as exc:
            logger.error("Wake-word worker error: %s", exc)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            try:
                pa.terminate()
            except Exception:
                pass

    async def _trigger_wake_word_recording(self) -> None:
        """Async callback: record after wake word until VAD silence."""
        if self._pa is None or webrtcvad is None:
            logger.warning("Cannot record after wake word: pyaudio or webrtcvad missing.")
            return

        audio_buffer: List[bytes] = []
        stream: Optional[Any] = None
        vad = webrtcvad.Vad(self.audio_config.vad_aggressiveness)
        frame_duration_ms = self.audio_config.frame_duration_ms
        frame_size = int(self._sample_rate * frame_duration_ms / 1000)
        silence_timeout_ms = self.audio_config.silence_timeout_ms
        max_silence_frames = max(1, int(silence_timeout_ms / frame_duration_ms))
        silence_frames = 0

        try:
            stream = self._pa.open(
                rate=self._sample_rate,
                channels=self._channels,
                format=self._format,
                input=True,
                frames_per_buffer=frame_size,
            )
            print("[InputManager] Recording after wake word (VAD silence detection)...")

            while True:
                pcm = stream.read(frame_size, exception_on_overflow=False)
                if not pcm:
                    continue
                audio_buffer.append(pcm)
                is_speech = vad.is_speech(pcm, self._sample_rate)
                if is_speech:
                    silence_frames = 0
                else:
                    silence_frames += 1
                    if silence_frames >= max_silence_frames:
                        break
        except Exception as exc:
            logger.error("Wake-word recording error: %s", exc)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass

        audio_bytes = b"".join(audio_buffer)
        if audio_bytes:
            self._schedule_async(self._on_wake_word, audio_bytes)

    # ------------------------------------------------------------------
    # Audio stream helpers
    # ------------------------------------------------------------------

    def _open_stream(self) -> None:
        """Open the PyAudio input stream for hotkey recording."""
        if self._pa is None:
            return
        self._close_stream()
        try:
            self._stream = self._pa.open(
                rate=self._sample_rate,
                channels=self._channels,
                format=self._format,
                input=True,
                frames_per_buffer=self._chunk_size,
                stream_callback=self._audio_callback,
            )
            self._stream.start_stream()
        except Exception as exc:
            logger.error("Failed to open microphone stream: %s", exc)
            self._stream = None

    def _audio_callback(self, in_data: bytes, frame_count: int, time_info: Any, status: int) -> tuple:
        """PyAudio stream callback — appends to buffer while recording."""
        if self._is_recording and in_data:
            self._audio_buffer.append(in_data)
        return (None, pyaudio.paContinue)

    def _close_stream(self) -> None:
        """Close the active PyAudio stream safely."""
        if self._stream is not None:
            try:
                self._stream.stop_stream()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _stop_and_collect(self) -> bytes:
        """Stop stream and return concatenated audio with WAV header."""
        self._close_stream()
        raw_audio = b"".join(self._audio_buffer)
        self._audio_buffer.clear()
        return self._wrap_wav(raw_audio)

    def _wrap_wav(self, raw_audio: bytes) -> bytes:
        """Wrap raw PCM bytes in a WAV file header (mono, 16-bit, 16kHz)."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self._channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self._sample_rate)
            wf.writeframes(raw_audio)
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Thread → async bridge
    # ------------------------------------------------------------------

    def _schedule_async(
        self,
        coro_fn: Optional[Callable[..., Coroutine[Any, Any, Any]]],
        *args: Any,
    ) -> None:
        """Safely schedule a coroutine from a background thread."""
        if coro_fn is None:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.warning("No event loop available to schedule async callback.")
            return
        try:
            asyncio.run_coroutine_threadsafe(coro_fn(*args), loop)
        except Exception as exc:
            logger.error("Failed to schedule async callback: %s", exc)

    # ------------------------------------------------------------------
    # Single utterance capture (for confirmation loops)
    # ------------------------------------------------------------------

    async def capture_single_utterance(self) -> str:
        """Record a single utterance after a trigger (e.g., confirmation response) and transcribe it."""
        if self._pa is None or webrtcvad is None:
            logger.warning("Cannot capture utterance: pyaudio or webrtcvad missing.")
            return ""

        audio_buffer: List[bytes] = []
        stream: Optional[Any] = None
        vad = webrtcvad.Vad(self.audio_config.vad_aggressiveness)
        frame_duration_ms = self.audio_config.frame_duration_ms
        frame_size = int(self._sample_rate * frame_duration_ms / 1000)
        silence_timeout_ms = self.audio_config.silence_timeout_ms
        max_silence_frames = max(1, int(silence_timeout_ms / frame_duration_ms))
        silence_frames = 0

        try:
            stream = self._pa.open(
                rate=self._sample_rate,
                channels=self._channels,
                format=self._format,
                input=True,
                frames_per_buffer=frame_size,
            )
            print("[InputManager] Listening for confirmation response...")

            while True:
                pcm = stream.read(frame_size, exception_on_overflow=False)
                if not pcm:
                    continue
                audio_buffer.append(pcm)
                is_speech = vad.is_speech(pcm, self._sample_rate)
                if is_speech:
                    silence_frames = 0
                else:
                    silence_frames += 1
                    if silence_frames >= max_silence_frames:
                        break
        except Exception as exc:
            logger.error("capture_single_utterance error: %s", exc)
            return ""
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass

        audio_bytes = self._wrap_wav(b"".join(audio_buffer))
        return await self.transcribe_audio(audio_bytes)

    # ------------------------------------------------------------------
    # STT transcription
    # ------------------------------------------------------------------

    async def transcribe_audio(self, audio_bytes: bytes) -> str:
        """Save audio bytes to a temporary WAV and transcribe via Groq Whisper.

        In dry-run mode returns a canned transcription without calling the API.
        """
        if self.config.app.dry_run:
            logger.info("[dry-run] Returning mock transcription.")
            return "open chrome"

        if self._groq_client is None:
            logger.error("Groq client not initialised (missing API key or groq package).")
            return ""

        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = Path(tmp.name)

            def _transcribe() -> Any:
                with open(tmp_path, "rb") as audio_file:
                    return self._groq_client.audio.transcriptions.create(
                        file=audio_file,
                        model=self.stt_config.model,
                        language=self.stt_config.language,
                    )

            response = await asyncio.to_thread(_transcribe)
            transcription: str = response.text if hasattr(response, "text") else str(response)
            logger.info("Transcription: %s", transcription)
            return transcription.strip()
        except Exception as exc:
            logger.error("Transcription failed: %s", exc)
            return ""
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
