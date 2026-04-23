"""Main entry point for VoiceUse desktop voice agent.

Usage:
    python -m voiceuse

The launcher initialises all subsystems, wires callbacks, and runs the
async event loop until Ctrl+C (SIGINT) is received.
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, Optional

from voiceuse.config import Config
from voiceuse.models import CommandResult

# Subsystems (assumed to exist in the voiceuse package)
from voiceuse.input_manager import InputManager
from voiceuse.tts_manager import TTSManager
from voiceuse.os_controller import OSController
from voiceuse.vision_bridge import VisionBridge
from voiceuse.safety import SafetyGuard
from voiceuse.brain import Brain, LLMError

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

def _setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger with a nice console format."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers if this module is re-imported
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)


# ---------------------------------------------------------------------------
# Callbacks wired to InputManager
# ---------------------------------------------------------------------------

async def _on_hotkey_press() -> None:
    """User pressed and held the hotkey — InputManager started capturing audio."""
    logger.info("Hotkey pressed — recording started by InputManager.")


async def _on_hotkey_release(audio_bytes: bytes) -> None:
    """User released the hotkey — audio captured, transcribe and execute."""
    logger.info("Hotkey released — processing %d bytes of audio.", len(audio_bytes))
    if audio_bytes:
        asyncio.create_task(_pipeline(audio_bytes))


async def _on_wake_word_detected(audio_bytes: bytes) -> None:
    """Wake-word triggered — same pipeline as hotkey release."""
    logger.info("Wake word detected — processing %d bytes of audio.", len(audio_bytes))
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
    except Exception as exc:
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
        raise RuntimeError("InputManager not available for confirmation capture.")
    return await _input_manager.capture_single_utterance()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

async def _init_subsystems(cfg: Config) -> None:
    """Instantiate and connect all VoiceUse subsystems."""
    global _input_manager, _tts_manager, _os_controller
    global _vision_bridge, _safety_guard, _brain, _config

    _config = cfg

    logger.info("Initialising VoiceUse subsystems...")

    # Input
    _input_manager = InputManager(config=cfg)
    _input_manager.register_callbacks(
        on_hotkey_start=_on_hotkey_press,
        on_hotkey_stop=_on_hotkey_release,
        on_wake_word=_on_wake_word_detected,
    )

    # TTS
    _tts_manager = TTSManager(config=cfg)
    await _tts_manager.start()

    # OS / Vision / Safety
    _os_controller = OSController(config=cfg)
    _vision_bridge = VisionBridge(
        config=cfg,
        os_controller=_os_controller,
    )
    _safety_guard = SafetyGuard(config=cfg)

    # Brain (orchestrator)
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
    _setup_logging()
    logger.info("=" * 50)
    logger.info("VoiceUse starting up...")
    logger.info("=" * 50)

    # 1. Config
    cfg = _ensure_config()
    logger.info("Config loaded (STT=%s, LLM=%s).", cfg.stt.provider, cfg.llm.provider)

    # 2. Subsystems
    await _init_subsystems(cfg)

    # 3. Register SIGINT / SIGTERM for graceful exit
    def _signal_handler(sig: int, _frame: Any) -> None:
        logger.info("Received signal %s — shutting down.", sig)
        _shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # 4. Start InputManager (blocking-ish, but we run it in a task)
    if _input_manager is None:
        raise RuntimeError("InputManager not initialised.")

    input_task = asyncio.create_task(_input_manager.start())

    # 5. Print user-facing status
    print()
    print(" VoiceUse is running!")
    print(f"   Hotkey : {cfg.audio.hotkey}")
    print(f"   Wake   : {cfg.audio.wake_word}")
    print("   Press Ctrl+C to quit.")
    print()

    # 6. Idle until shutdown
    try:
        await _shutdown_event.wait()
    except asyncio.CancelledError:
        pass

    # 7. Graceful teardown
    logger.info("Shutting down VoiceUse...")

    # Cancel the input manager task
    if not input_task.done():
        input_task.cancel()
        try:
            await input_task
        except asyncio.CancelledError:
            pass

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

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Expected when user hits Ctrl+C before signal handler fires
        sys.exit(0)
    except Exception as exc:
        logger.exception("Fatal error in main: %s", exc)
        sys.exit(1)
