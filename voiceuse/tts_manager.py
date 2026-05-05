"""TTS Manager for VoiceUse: cancellable text-to-speech output.

Voice assistants need a stricter audio contract than "play every sentence in
order." The user may start speaking while the assistant is talking, or a later
result may make an earlier phrase stale. This manager owns the speech queue and
the active playback backend so the rest of the application can explicitly stop
old audio before continuing.
"""
from __future__ import annotations

import asyncio
import logging
import platform as pf
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

try:
    import edge_tts
except ImportError:  # pragma: no cover
    edge_tts = None  # type: ignore[assignment]

try:
    import pyttsx3
except ImportError:  # pragma: no cover
    pyttsx3 = None  # type: ignore[assignment]

try:
    import pygame
except ImportError:  # pragma: no cover
    pygame = None  # type: ignore[assignment]

try:
    from pydub import AudioSegment
    from pydub.playback import play as pydub_play
except ImportError:  # pragma: no cover
    AudioSegment = None  # type: ignore[assignment,misc]
    pydub_play = None  # type: ignore[assignment]

from voiceuse.config import Config

logger = logging.getLogger("voiceuse.tts_manager")


class TTSManager:
    """Manages text-to-speech output with edge-tts primary and pyttsx3 fallback."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.tts_config = config.tts

        # Speech job queue
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._cancel_event = threading.Event()
        self._current_process: Optional[asyncio.subprocess.Process] = None

        # pyttsx3 fallback engine (initialised lazily in worker thread)
        self._pyttsx3_engine: Optional[Any] = None
        self._pyttsx3_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background queue worker."""
        self._stop_event.clear()
        self._worker_task = asyncio.create_task(
            self._queue_worker(), name="tts-worker"
        )
        print("[TTSManager] Worker started.")

    async def stop(self) -> None:
        """Stop the worker and drain the queue."""
        logger.info("TTSManager stopping...")
        self._stop_event.set()
        await self.cancel()

        # Cancel any pending worker
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        # Stop pygame mixer if initialised
        if pygame is not None and pygame.mixer.get_init():
            try:
                pygame.mixer.quit()
            except Exception as exc:
                logger.debug("Error quitting pygame mixer: %s", exc)

        print("[TTSManager] Stopped.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def speak(self, text: str, *, interrupt: bool = False) -> None:
        """Enqueue text to be spoken.

        Args:
            text: Natural-language phrase to synthesize.
            interrupt: When true, cancel the active playback and clear queued
                stale speech before adding this phrase. This is the path to use
                when a new voice turn should take precedence over old audio.
        """
        if not text.strip():
            return
        if not self.tts_config.enabled:
            logger.debug("TTS is disabled; skipping: %s", text)
            return
        if interrupt:
            await self.cancel()
        await self._queue.put(text)

    async def cancel(self) -> None:
        """Stop active speech and remove queued speech that has gone stale."""
        self._cancel_event.set()
        self._drain_queue()
        self._terminate_current_process()
        await asyncio.to_thread(self._stop_threaded_backends)

    def _drain_queue(self) -> None:
        """Remove all queued speech jobs and mark them complete."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

    # ------------------------------------------------------------------
    # Queue worker
    # ------------------------------------------------------------------

    async def _queue_worker(self) -> None:
        """Consume the speech queue and invoke the appropriate TTS engine."""
        while not self._stop_event.is_set():
            try:
                text = await asyncio.wait_for(
                    self._queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue

            if self._stop_event.is_set():
                self._queue.task_done()
                break

            if not self.tts_config.enabled:
                self._queue.task_done()
                continue

            self._cancel_event.clear()
            success = await self._speak_with_edge_tts(text)
            if not success and not self._cancel_event.is_set():
                success = await self._speak_with_pyttsx3(text)
            if not success and not self._cancel_event.is_set():
                logger.error("All TTS engines failed for text: %s", text)
                print(f"[TTSManager] ERROR: Could not speak: {text}")

            self._queue.task_done()

    # ------------------------------------------------------------------
    # Primary: edge-tts
    # ------------------------------------------------------------------

    async def _speak_with_edge_tts(self, text: str) -> bool:
        """Synthesize speech with edge-tts and play the resulting MP3."""
        if edge_tts is None:
            logger.debug("edge_tts not installed; skipping.")
            return False

        tmp_path: Optional[Path] = None
        try:
            communicate = edge_tts.Communicate(
                text,
                voice=self.tts_config.voice,
                rate=self.tts_config.speed,
            )
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            await communicate.save(str(tmp_path))

            played = await self._play_audio_file(str(tmp_path))
            return played
        except Exception as exc:
            logger.warning("edge_tts synthesis failed: %s", exc)
            return False
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Fallback: pyttsx3
    # ------------------------------------------------------------------

    async def _speak_with_pyttsx3(self, text: str) -> bool:
        """Synthesize speech with pyttsx3 (blocking → run in thread)."""
        if pyttsx3 is None:
            logger.debug("pyttsx3 not installed; skipping.")
            return False

        try:
            await asyncio.to_thread(self._pyttsx3_speak_sync, text)
            return True
        except Exception as exc:
            logger.warning("pyttsx3 synthesis failed: %s", exc)
            return False

    def _pyttsx3_speak_sync(self, text: str) -> None:
        """Thread-safe synchronous pyttsx3 speak."""
        engine = pyttsx3.init()
        with self._pyttsx3_lock:
            self._pyttsx3_engine = engine
        try:
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception as exc:
            logger.warning("pyttsx3 engine error: %s", exc)
            raise
        finally:
            with self._pyttsx3_lock:
                if self._pyttsx3_engine is engine:
                    self._pyttsx3_engine = None

    # ------------------------------------------------------------------
    # Audio playback helpers
    # ------------------------------------------------------------------

    async def _play_audio_file(self, file_path: str) -> bool:
        """Try multiple playback backends and return True on success."""
        # 1. ffplay (ffmpeg) — most reliable cross-platform if installed
        if await self._try_ffplay(file_path):
            return True

        # 2. mpv — popular media player
        if await self._try_mpv(file_path):
            return True

        # 3. pygame.mixer — lightweight fallback
        if await self._try_pygame(file_path):
            return True

        # 4. pydub + simpleaudio — another fallback
        if await self._try_pydub(file_path):
            return True

        # 5. platform-specific
        if pf.system() == "Darwin" and await self._try_afplay(file_path):
            return True

        logger.error("No available audio playback backend found.")
        return False

    async def _try_ffplay(self, file_path: str) -> bool:
        """Play with ffplay (part of ffmpeg)."""
        if shutil.which("ffplay") is None:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel", "quiet",
                file_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._current_process = proc
            await proc.wait()
            return proc.returncode == 0
        except Exception as exc:
            logger.debug("ffplay failed: %s", exc)
            return False
        finally:
            self._current_process = None

    async def _try_mpv(self, file_path: str) -> bool:
        """Play with mpv media player."""
        if shutil.which("mpv") is None:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "mpv",
                "--no-video",
                "--really-quiet",
                file_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._current_process = proc
            await proc.wait()
            return proc.returncode == 0
        except Exception as exc:
            logger.debug("mpv failed: %s", exc)
            return False
        finally:
            self._current_process = None

    async def _try_pygame(self, file_path: str) -> bool:
        """Play with pygame.mixer."""
        if pygame is None:
            return False
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._pygame_play_sync, file_path)
            return True
        except Exception as exc:
            logger.debug("pygame playback failed: %s", exc)
            return False

    def _pygame_play_sync(self, file_path: str) -> None:
        """Synchronous pygame playback (called in executor)."""
        if pygame.mixer.get_init() is None:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
        pygame.mixer.music.load(file_path)
        pygame.mixer.music.play()
        # Poll the shared cancellation event so user interruptions stop playback.
        while pygame.mixer.music.get_busy() and not self._cancel_event.is_set():
            pygame.time.wait(50)
        if self._cancel_event.is_set():
            pygame.mixer.music.stop()
        pygame.mixer.music.unload()

    async def _try_pydub(self, file_path: str) -> bool:
        """Play with pydub + simpleaudio."""
        if AudioSegment is None or pydub_play is None:
            return False
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._pydub_play_sync, file_path)
            return True
        except Exception as exc:
            logger.debug("pydub playback failed: %s", exc)
            return False

    def _pydub_play_sync(self, file_path: str) -> None:
        """Synchronous pydub playback (called in executor)."""
        audio = AudioSegment.from_mp3(file_path)
        pydub_play(audio)

    async def _try_afplay(self, file_path: str) -> bool:
        """Play with afplay (macOS built-in)."""
        if shutil.which("afplay") is None:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "afplay", file_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._current_process = proc
            await proc.wait()
            return proc.returncode == 0
        except Exception as exc:
            logger.debug("afplay failed: %s", exc)
            return False
        finally:
            self._current_process = None

    def _terminate_current_process(self) -> None:
        """Terminate subprocess playback backends from the event-loop thread."""
        proc = self._current_process
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        except Exception as exc:
            logger.debug("Failed to terminate TTS playback process: %s", exc)

    def _stop_threaded_backends(self) -> None:
        """Stop playback engines that run in executor threads."""
        if pygame is not None and pygame.mixer.get_init():
            try:
                pygame.mixer.music.stop()
            except Exception as exc:
                logger.debug("Failed to stop pygame playback: %s", exc)

        with self._pyttsx3_lock:
            if self._pyttsx3_engine is not None:
                try:
                    self._pyttsx3_engine.stop()
                except Exception as exc:
                    logger.debug("Failed to stop pyttsx3 playback: %s", exc)
