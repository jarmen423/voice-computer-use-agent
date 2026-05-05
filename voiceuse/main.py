"""Application entry point for the VoiceUse desktop voice agent.

This module is the composition root for the local app.  It owns process-level
concerns such as configuration, logging, subsystem construction, signal
handling, and graceful teardown.  The durable runtime state lives on
``Application`` instead of module-level globals so callbacks, tests, and future
UI surfaces can work with an explicit object graph.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import signal
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from voiceuse.brain import Brain, LLMError
from voiceuse.audio_device import AudioDevice
from voiceuse.config import Config
from voiceuse.health import check_installation, print_report
from voiceuse.input_manager import InputManager
from voiceuse.observability import LatencyTimer
from voiceuse.os_controller import OSController
from voiceuse.plugins import get_plugin
from voiceuse.safety import SafetyGuard
from voiceuse.tts_manager import TTSManager
from voiceuse.vision_bridge import VisionBridge

logger = logging.getLogger("voiceuse.main")

DEFAULT_CONFIG_PATH = Path("config.yaml")


class ApplicationState(str, Enum):
    """High-level runtime states for the VoiceUse application."""

    CREATED = "created"
    INITIALISING = "initialising"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    ACTING = "acting"
    SPEAKING = "speaking"
    CONFIRMING = "confirming"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


def _ensure_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load config from YAML and create a default config when missing."""
    if not path.exists():
        logger.info("Config not found at %s; creating default.", path.resolve())
        cfg = Config()
        cfg.to_yaml(str(path))
        return cfg
    return Config.from_yaml(str(path))


def _setup_logging(
    level: int = logging.INFO,
    log_file: Optional[Path] = None,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """Configure console logging and optional rotating file logging.

    Args:
        level: Root logging threshold.
        log_file: Optional file destination for persistent logs.
        max_bytes: Maximum size of each rotating log file.
        backup_count: Number of old log files to retain.
    """
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                str(log_file),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            handler.setLevel(level)
            handler.setFormatter(fmt)
            root.addHandler(handler)
            logger.info("Logging to file: %s", log_file.resolve())
        except Exception as exc:
            logger.warning("Could not create file logger: %s", exc)


class Application:
    """Owns VoiceUse subsystem lifecycle and voice command processing.

    The hotkey and wake-word listeners call back into this object.  The object
    tracks all tasks it starts, which makes shutdown observable and cancellable
    instead of relying on module-level mutable globals.
    """

    def __init__(self, config: Config) -> None:
        """Create an application shell around a loaded configuration."""
        self.config = config
        self.shutdown_event = asyncio.Event()
        self.state = ApplicationState.CREATED

        self.input_manager: Optional[InputManager] = None
        self.tts_manager: Optional[TTSManager] = None
        self.os_controller: Optional[OSController] = None
        self.vision_bridge: Optional[VisionBridge] = None
        self.safety_guard: Optional[SafetyGuard] = None
        self.brain: Optional[Brain] = None
        self.active_plugin: Optional[Any] = None
        self.audio_device = AudioDevice()

        self.input_task: Optional[asyncio.Task[None]] = None
        self.pipeline_tasks: set[asyncio.Task[None]] = set()

    async def initialise(self) -> None:
        """Construct and wire all subsystems for the selected runtime mode."""
        self._set_state(ApplicationState.INITIALISING)
        logger.info("Initialising VoiceUse subsystems...")

        self.os_controller = OSController(config=self.config)
        self.vision_bridge = VisionBridge(
            config=self.config,
            os_controller=self.os_controller,
        )
        self.safety_guard = SafetyGuard(config=self.config)
        self.tts_manager = TTSManager(config=self.config)
        await self.tts_manager.start()

        plugin = get_plugin(self.config)
        if plugin is not None:
            logger.info("Activating plugin: %s", plugin.name)
            self.active_plugin = plugin
            await plugin.on_enable(
                config=self.config,
                os_controller=self.os_controller,
                vision_bridge=self.vision_bridge,
                safety_guard=self.safety_guard,
                tts_manager=self.tts_manager,
                get_confirmation_text=self.get_confirmation_text,
                audio_device=self.audio_device,
            )
            print(f"[Plugin] {plugin.name} active - default STT/LLM pipeline disabled.")
            self._set_state(ApplicationState.IDLE)
            return

        self.input_manager = InputManager(config=self.config, audio_device=self.audio_device)
        self.input_manager.register_callbacks(
            on_hotkey_start=self.on_hotkey_press,
            on_hotkey_stop=self.on_hotkey_release,
            on_wake_word=self.on_wake_word_detected,
        )

        self.brain = Brain(
            config=self.config,
            safety=self.safety_guard,
            os_controller=self.os_controller,
            vision_bridge=self.vision_bridge,
            tts_manager=self.tts_manager,
            get_confirmation_text=self.get_confirmation_text,
        )

        logger.info("All subsystems initialised.")
        self._set_state(ApplicationState.IDLE)

    async def run(self) -> None:
        """Start input processing and run until a shutdown signal arrives."""
        if self.active_plugin is not None:
            self.input_task = asyncio.create_task(self.active_plugin.run(), name="plugin-loop")
        elif self.input_manager is not None:
            self.input_task = asyncio.create_task(self.input_manager.start(), name="input-loop")
        else:
            raise RuntimeError("No input source initialised.")

        self._print_status()

        try:
            await self.shutdown_event.wait()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Cancel running tasks and stop subsystems in reverse ownership order."""
        logger.info("Shutting down VoiceUse...")
        self._set_state(ApplicationState.SHUTTING_DOWN)
        self.shutdown_event.set()

        if self.input_task is not None and not self.input_task.done():
            self.input_task.cancel()
            try:
                await self.input_task
            except asyncio.CancelledError:
                pass

        for task in list(self.pipeline_tasks):
            if not task.done():
                task.cancel()
        if self.pipeline_tasks:
            await asyncio.gather(*self.pipeline_tasks, return_exceptions=True)
            self.pipeline_tasks.clear()

        if self.active_plugin is not None:
            try:
                await self.active_plugin.on_disable()
            except Exception as exc:
                logger.warning("Plugin disable error: %s", exc)

        if self.input_manager is not None:
            try:
                await self.input_manager.stop()
            except Exception as exc:
                logger.warning("InputManager stop error: %s", exc)

        if self.tts_manager is not None:
            try:
                await self.tts_manager.stop()
            except Exception as exc:
                logger.warning("TTSManager stop error: %s", exc)

        self.audio_device.stop()

        logger.info("VoiceUse stopped. Goodbye!")
        self._set_state(ApplicationState.STOPPED)

    async def on_hotkey_press(self) -> None:
        """Handle hotkey press notifications from the input subsystem."""
        self._set_state(ApplicationState.LISTENING)
        logger.info("Hotkey pressed - recording started by InputManager.")
        if self.tts_manager is not None:
            await self.tts_manager.cancel()
        if self.active_plugin is not None and hasattr(self.active_plugin, "on_hotkey_press"):
            await self.active_plugin.on_hotkey_press()

    async def on_hotkey_release(self, audio_bytes: bytes) -> None:
        """Handle hotkey release and schedule command processing."""
        logger.info("Hotkey released - processing %d bytes of audio.", len(audio_bytes))
        if self.active_plugin is not None and hasattr(self.active_plugin, "on_hotkey_release"):
            await self.active_plugin.on_hotkey_release(audio_bytes)
            return
        if audio_bytes:
            self._start_pipeline_task(audio_bytes)

    async def on_wake_word_detected(self, audio_bytes: bytes) -> None:
        """Handle wake-word audio and schedule command processing."""
        logger.info("Wake word detected - processing %d bytes of audio.", len(audio_bytes))
        if self.tts_manager is not None:
            await self.tts_manager.cancel()
        if self.active_plugin is not None and hasattr(self.active_plugin, "on_wake_word"):
            await self.active_plugin.on_wake_word(audio_bytes)
            return
        if audio_bytes:
            self._start_pipeline_task(audio_bytes)

    async def get_confirmation_text(self) -> str:
        """Capture and transcribe a short spoken confirmation response."""
        self._set_state(ApplicationState.CONFIRMING)
        if self.input_manager is None:
            logger.warning("InputManager not available for confirmation capture.")
            return ""
        try:
            return await self.input_manager.capture_single_utterance()
        finally:
            self._set_state(ApplicationState.IDLE)

    async def speak(self, text: str, *, interrupt: bool = False) -> None:
        """Speak text through the configured TTS manager, swallowing output errors."""
        if self.tts_manager is None or not text:
            return
        self._set_state(ApplicationState.SPEAKING)
        try:
            await self.tts_manager.speak(text, interrupt=interrupt)
        except Exception as exc:
            logger.warning("TTS speak failed: %s", exc)
        finally:
            self._set_state(ApplicationState.IDLE)

    async def pipeline(self, audio_bytes: bytes) -> None:
        """Run the default STT -> Brain -> TTS command pipeline."""
        if self.tts_manager is None or self.brain is None or self.input_manager is None:
            logger.error("Subsystems not initialised; skipping pipeline.")
            return

        pipeline_timer = LatencyTimer("pipeline.total", detail=f"audio_bytes={len(audio_bytes)}")
        try:
            stt_timer = LatencyTimer("pipeline.stt")
            text = await self.input_manager.transcribe_audio(audio_bytes)
            stt_timer.finish(success=True, detail=f"chars={len(text)}")
        except Exception as exc:
            stt_timer.finish(success=False, detail=type(exc).__name__)
            pipeline_timer.finish(success=False, detail="stt_failed")
            logger.error("Transcription step failed: %s", exc)
            await self.speak("Sorry, I didn't catch that.", interrupt=True)
            return

        if not text:
            pipeline_timer.finish(success=True, detail="empty_transcription")
            logger.info("Empty transcription; nothing to do.")
            await self.speak("I didn't hear anything.", interrupt=True)
            return

        logger.info("Transcribed: %r", text)

        try:
            self._set_state(ApplicationState.THINKING)
            brain_timer = LatencyTimer("pipeline.brain", detail=text[:80])
            result = await self.brain.process_command(text)
            brain_timer.finish(success=result.success, detail=result.message[:120])
        except LLMError as exc:
            brain_timer.finish(success=False, detail=type(exc).__name__)
            pipeline_timer.finish(success=False, detail="llm_failed")
            logger.error("Brain LLM error: %s", exc)
            await self.speak("I'm having trouble reaching my language model right now.", interrupt=True)
            return
        except Exception:
            brain_timer.finish(success=False, detail="unexpected")
            pipeline_timer.finish(success=False, detail="brain_failed")
            logger.exception("Brain processing error")
            await self.speak("Something went wrong while trying to help.", interrupt=True)
            return

        await self.speak(result.message, interrupt=True)
        pipeline_timer.finish(success=result.success, detail=result.message[:120])

    def request_shutdown(self, sig: int) -> None:
        """Signal-safe shutdown entrypoint used by process signal handlers."""
        logger.info("Received signal %s; shutting down.", sig)
        self.shutdown_event.set()

    def _set_state(self, state: ApplicationState) -> None:
        """Update and log the high-level application state."""
        if self.state == state:
            return
        logger.debug("Application state: %s -> %s", self.state.value, state.value)
        self.state = state

    def _start_pipeline_task(self, audio_bytes: bytes) -> None:
        """Start and track one voice command pipeline task."""
        task = asyncio.create_task(self.pipeline(audio_bytes), name="voice-command-pipeline")
        self.pipeline_tasks.add(task)
        task.add_done_callback(self.pipeline_tasks.discard)

    def _print_status(self) -> None:
        """Print concise runtime status for the user."""
        print()
        print(" VoiceUse is running!")
        print(f"   Hotkey : {self.config.audio.hotkey}")
        print(f"   Wake   : {self.config.audio.wake_word}")
        if self.active_plugin is not None:
            print(f"   Plugin : {self.active_plugin.name}")
        print("   Press Ctrl+C to quit.")
        print()


def _parse_args() -> argparse.Namespace:
    """Parse command-line flags for the console entrypoint."""
    parser = argparse.ArgumentParser(description="VoiceUse desktop voice agent")
    parser.add_argument("--dry-run", action="store_true", help="Run with mock LLM/STT responses")
    parser.add_argument("--check-install", action="store_true", help="Check dependencies and exit")
    parser.add_argument("--log-file", type=Path, default=None, help="Path to rotating log file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging")
    return parser.parse_args()


async def main() -> None:
    """Configure, construct, and run the VoiceUse application."""
    args = _parse_args()
    level = logging.DEBUG if args.verbose else logging.INFO
    _setup_logging(level=level, log_file=args.log_file)

    logger.info("=" * 50)
    logger.info("VoiceUse starting up...")
    logger.info("=" * 50)

    cfg = _ensure_config()
    report = check_installation(cfg)
    print_report(report)
    if args.check_install:
        sys.exit(0 if report.ok else 1)

    if args.dry_run:
        cfg.app.dry_run = True
        logger.info("Dry-run mode enabled - using mock responses.")

    logger.info("Config loaded (STT=%s, LLM=%s).", cfg.stt.provider, cfg.llm.provider)

    app = Application(config=cfg)
    await app.initialise()

    def _signal_handler(sig: int, _frame: Any) -> None:
        app.request_shutdown(sig)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    await app.run()


def _entry() -> None:
    """Synchronous console-script entrypoint."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        logging.getLogger("voiceuse.main").exception("Fatal error in main: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    _entry()
