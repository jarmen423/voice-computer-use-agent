"""Plugin base class for VoiceUse.

Defines the lifecycle hooks that every plugin must implement so that
``main.py`` can initialise, run, and tear down plugins uniformly.
"""

import abc
from typing import Any, Awaitable, Callable, Optional

from voiceuse.config import Config
from voiceuse.os_controller import OSController
from voiceuse.vision_bridge import VisionBridge
from voiceuse.safety import SafetyGuard


class PluginBase(abc.ABC):
    """Abstract base class for VoiceUse plugins.

    A plugin may replace the default Brain + InputManager STT + TTSManager
    pipeline (e.g. Grok Voice Realtime API).  The host application
    (``main.py``) calls :meth:`on_enable` at startup and :meth:`on_disable`
    at shutdown.  While active, the host schedules :meth:`run` as the main
    asyncio task.
    """

    name: str = "unnamed_plugin"

    @abc.abstractmethod
    async def on_enable(
        self,
        config: Config,
        os_controller: OSController,
        vision_bridge: VisionBridge,
        safety_guard: SafetyGuard,
        tts_manager: Any,
        get_confirmation_text: Callable[[], Awaitable[str]],
    ) -> None:
        """Called once when the plugin is activated.

        Args:
            config: The loaded application configuration.
            os_controller: Shared OS automation controller.
            vision_bridge: Shared computer-vision bridge.
            safety_guard: Shared safety guard for destructive actions.
            tts_manager: Shared TTS manager (for fallback announcements).
            get_confirmation_text: Async callable that captures a spoken
                confirmation utterance.
        """

    @abc.abstractmethod
    async def on_disable(self) -> None:
        """Called once when the application shuts down."""

    @abc.abstractmethod
    async def run(self) -> None:
        """Main asyncio task for the plugin.

        This should run until the application signals shutdown, at which
        point the method should return cleanly so ``on_disable`` can be
        invoked.
        """

    @abc.abstractmethod
    def is_active(self) -> bool:
        """Return whether the plugin is currently active and processing."""

    # Optional hotkey hooks (called by main.py if present)
    async def on_hotkey_press(self) -> None:
        """User pressed the configured hotkey."""

    async def on_hotkey_release(self, audio_bytes: bytes) -> None:
        """User released the hotkey (with captured audio if applicable)."""

    async def on_wake_word(self, audio_bytes: bytes) -> None:
        """Wake word detected (with captured audio if applicable)."""
