"""Main entry point for VoiceUse desktop voice agent.

Usage:
    python -m voiceuse
    python -m voiceuse --dry-run
    voiceuse --check-install

The launcher initialises all subsystems, wires callbacks, and runs the
async event loop until Ctrl+C (SIGINT) is received.
"""

import argparse
import asyncio
import logging
import logging.handlers
import signal
import sys
from pathlib import Path
from typing import Any, Optional

from voiceuse.config import Config

# Subsystems (assumed to exist in the voiceuse package)
from voiceuse.input_manager import InputManager
from voiceuse.tts_manager import TTSManager
from voiceuse.os_controller import OSController
from voiceuse.vision_bridge import VisionBridge
from voiceuse.safety import SafetyGuard
from voiceuse.brain import Brain, LLMError
from voiceuse.health import check_installation, print_report
from voiceuse.plugins import get_plugin

# Optional Groq SDK for Whisper transcription
# (also used inside InputManager; imported here only for fast-fail check)
try:
    import groq
except ImportError:
    groq = None  # type: ignore

logger = logging.getLogger("voiceuse.main")

# ---------------------------------------------------------------------------
# Global references for graceful shutdown
# ---------------------------------------------------------------------------
_shutdown_event: asyncio.Event = asyncio.Event()
_input_manager: Optional[InputManager] = None
_tts_manager: Optional[TTSManager] = None
_os_controller: Optional[OSController] = None
_vision_bridge: Optional[VisionBridge] = None
_safety_guard: Optional[SafetyGuard] = None
_brain: Optional[Brain] = None
_config: Optional[Config] = None
_active_plugin: Optional[Any] = None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = Path("config.yaml")


def _ensure_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load config from YAML; write a default file if missing."""
    if not path.exists():
        logger.info("Config not found at %s — creating default.", path.resolve())
        cfg = Config()
        cfg.to_yaml(str(path))
        return cfg
    return Config.from_yaml(str(path))


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(
    level: int = logging.INFO,
    log_file: Optional[Path] = None,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """Configure root logger with console and optional rotating file output.

    Args:
        level: Logging threshold (e.g. ``logging.INFO``).
        log_file: Path to a log file. If provided, a
            :class:`logging.handlers.RotatingFileHandler` is added.
        max_bytes: Maximum size of a single log file before rotation.
        backup_count: Number of backup files to retain.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Prevent duplicate handlers on re-entry
    if root.handlers:
        root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    # Optional rotating file
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                str(log_file), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
            logger.info("Logging to file: %s", log_file.resolve())
        except Exception as exc:
            logger.warning("Could not create file logger: %s", exc)


# ---------------------------------------------------------------------------
# Callbacks wired to InputManager
# ---------------------------------------------------------------------------

async def _on_hotkey_press() -> None:
    """User pressed and held the hotkey — InputManager started capturing audio."""
    logger.info("Hotkey pressed — recording started by InputManager.")
    if _active_plugin is not None and hasattr(_active_plugin, "on_hotkey_press"):
        await _active_plugin.on_hotkey_press()


async def _on_hotkey_release(audio_bytes: bytes) -> None:
    """User released the hotkey — audio captured, transcribe and execute."""
    logger.info("Hotkey released — processing %d bytes of audio.", len(audio_bytes))
    if _active_plugin is not None and hasattr(_active_plugin, "on_hotkey_release"):
        await _active_plugin.on_hotkey_release(audio_bytes)
        return
    if audio_bytes:
        asyncio.create_task(_pipeline(audio_bytes))


async def _on_wake_word_detected(audio_bytes: bytes) -> None:
    """Wake-word triggered — same pipeline as hotkey release."""
    logger.info("Wake word detected — processing %d bytes of audio.", len(audio_bytes))
    if _active_plugin is not None and hasattr(_active_plugin, "on_wake_word"):
        await _active_plugin.on_wake_word(audio_bytes)
        return
    if audio_bytes:
        asyncio.create_task(_pipeline(audio_bytes))


# ---------------------------------------------------------------------------
# Core async pipeline
# ---------------------------------------------------------------------------

async def _pipeline(audio_bytes: bytes) -> None:
    """Transcribe → Brain.process_command → TTS speak."""
    if _tts_manager is None or _brain is None or _input_manager is None:
        logger.error("Subsystems not initialised — skipping pipeline.")
        return

    # 1. Transcribe
    try:
        text = await _input_manager.transcribe_audio(audio_bytes)
    except Exception as exc:
        logger.error("Transcription step failed: %s", exc)
        await _speak("Sorry, I didn't catch that.")
        return

    if not text:
        logger.info("Empty transcription — nothing to do.")
        await _speak("I didn't hear anything.")
        return

    logger.info("Transcribed: %r", text)

    # 2. Brain plan + execute
    try:
        result = await _brain.process_command(text)
    except LLMError as exc:
        logger.error("Brain LLM error: %s", exc)
        await _speak("I'm having trouble reaching my language model right now.")
        return
    except Exception:
        logger.exception("Brain processing error")
        await _speak("Something went wrong while trying to help.")
        return

    # 3. Speak the result
    await _speak(result.message)


async def _speak(text: str) -> None:
    """Wrapper around TTSManager that swallows errors."""
    if _tts_manager is None or not text:
        return
    try:
        await _tts_manager.speak(text)
    except Exception as exc:
        logger.warning("TTS speak failed: %s", exc)


# ---------------------------------------------------------------------------
# Confirmation helper for safety guard
# ---------------------------------------------------------------------------

async def _get_confirmation_text() -> str:
    """Record a short utterance and transcribe it (used by SafetyGuard)."""
    if _input_manager is None:
        logger.warning("InputManager not available for confirmation capture.")
        return ""
    return await _input_manager.capture_single_utterance()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

async def _init_subsystems(cfg: Config) -> None:
    """Instantiate and connect all VoiceUse subsystems."""
    global _input_manager, _tts_manager, _os_controller
    global _vision_bridge, _safety_guard, _brain, _config, _active_plugin

    _config = cfg

    logger.info("Initialising VoiceUse subsystems...")

    # OS / Vision / Safety (shared by both default pipeline and plugins)
    _os_controller = OSController(config=cfg)
    _vision_bridge = VisionBridge(
        config=cfg,
        os_controller=_os_controller,
    )
    _safety_guard = SafetyGuard(config=cfg)
    _tts_manager = TTSManager(config=cfg)
    await _tts_manager.start()

    # Check if a plugin is enabled and should replace the default pipeline
    plugin = get_plugin(cfg)
    if plugin is not None:
        logger.info("Activating plugin: %s", plugin.name)
        _active_plugin = plugin
        await plugin.on_enable(
            config=cfg,
            os_controller=_os_controller,
            vision_bridge=_vision_bridge,
            safety_guard=_safety_guard,
            tts_manager=_tts_manager,
            get_confirmation_text=_get_confirmation_text,
        )
        print(f"[Plugin] {plugin.name} active — default STT/LLM/TTS pipeline disabled.")
        return

    # Default pipeline: Input → STT → Brain → TTS
    _input_manager = InputManager(config=cfg)
    _input_manager.register_callbacks(
        on_hotkey_start=_on_hotkey_press,
        on_hotkey_stop=_on_hotkey_release,
        on_wake_word=_on_wake_word_detected,
    )

    _brain = Brain(
        config=cfg,
        safety=_safety_guard,
        os_controller=_os_controller,
        vision_bridge=_vision_bridge,
        tts_manager=_tts_manager,
        get_confirmation_text=_get_confirmation_text,
    )

    logger.info("All subsystems initialised.")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def main() -> None:
    """Entry coroutine — configure, initialise, run, shutdown."""
    parser = argparse.ArgumentParser(description="VoiceUse desktop voice agent")
    parser.add_argument("--dry-run", action="store_true", help="Run with mock LLM/STT responses (no API calls)")
    parser.add_argument("--check-install", action="store_true", help="Check dependencies and exit")
    parser.add_argument("--log-file", type=Path, default=None, help="Path to rotating log file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    _setup_logging(level=level, log_file=args.log_file)
    logger.info("=" * 50)
    logger.info("VoiceUse starting up...")
    logger.info("=" * 50)

    # 1. Health check
    cfg = _ensure_config()
    report = check_installation(cfg)
    print_report(report)
    if args.check_install:
        sys.exit(0 if report.ok else 1)

    # 2. Dry-run override
    if args.dry_run:
        cfg.app.dry_run = True
        logger.info("Dry-run mode enabled — using mock responses.")

    logger.info("Config loaded (STT=%s, LLM=%s).", cfg.stt.provider, cfg.llm.provider)

    # 3. Subsystems
    await _init_subsystems(cfg)

    # 4. Register SIGINT / SIGTERM for graceful exit
    def _signal_handler(sig: int, _frame: Any) -> None:
        logger.info("Received signal %s — shutting down.", sig)
        _shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # 5. Start InputManager or plugin equivalent
    input_task: Optional[asyncio.Task[None]] = None
    if _active_plugin is not None:
        input_task = asyncio.create_task(_active_plugin.run(), name="plugin-loop")
    elif _input_manager is not None:
        input_task = asyncio.create_task(_input_manager.start(), name="input-loop")
    else:
        raise RuntimeError("No input source initialised.")

    # 6. Print user-facing status
    print()
    print(" VoiceUse is running!")
    print(f"   Hotkey : {cfg.audio.hotkey}")
    print(f"   Wake   : {cfg.audio.wake_word}")
    if _active_plugin:
        print(f"   Plugin : {_active_plugin.name}")
    print("   Press Ctrl+C to quit.")
    print()

    # 7. Idle until shutdown
    try:
        await _shutdown_event.wait()
    except asyncio.CancelledError:
        pass

    # 8. Graceful teardown
    logger.info("Shutting down VoiceUse...")

    if input_task is not None and not input_task.done():
        input_task.cancel()
        try:
            await input_task
        except asyncio.CancelledError:
            pass

    if _active_plugin is not None and hasattr(_active_plugin, "on_disable"):
        try:
            await _active_plugin.on_disable()
        except Exception as exc:
            logger.warning("Plugin disable error: %s", exc)

    if _input_manager is not None and hasattr(_input_manager, "stop"):
        try:
            await _input_manager.stop()
        except Exception as exc:
            logger.warning("InputManager stop error: %s", exc)

    if _tts_manager is not None and hasattr(_tts_manager, "stop"):
        try:
            if asyncio.iscoroutinefunction(_tts_manager.stop):
                await _tts_manager.stop()
            else:
                _tts_manager.stop()
        except Exception as exc:
            logger.warning("TTSManager stop error: %s", exc)

    if _os_controller is not None and hasattr(_os_controller, "stop"):
        try:
            if asyncio.iscoroutinefunction(_os_controller.stop):
                await _os_controller.stop()
            else:
                _os_controller.stop()
        except Exception as exc:
            logger.warning("OSController stop error: %s", exc)

    logger.info("VoiceUse stopped. Goodbye!")


# ---------------------------------------------------------------------------
# Script entry
# ---------------------------------------------------------------------------

def _entry() -> None:
    """Sync entry point for ``python -m voiceuse`` and console script."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        logging.getLogger("voiceuse.main").exception("Fatal error in main: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    _entry()
