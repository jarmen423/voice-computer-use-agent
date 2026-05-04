"""Plugin registry for VoiceUse.

Discovers and instantiates enabled plugins based on the loaded config.
Currently supports the Grok Voice Realtime API plugin.
"""

from typing import Optional

from voiceuse.config import Config
from voiceuse.plugins.base import PluginBase


def get_plugin(config: Config) -> Optional[PluginBase]:
    """Return the active plugin if one is enabled, otherwise ``None``.

    When ``None`` is returned, ``main.py`` falls back to the default
    Brain + InputManager + TTSManager pipeline.

    Args:
        config: The loaded application configuration.

    Returns:
        An initialised :class:`PluginBase` subclass instance, or ``None``.
    """
    if config.plugins.grok_voice.enabled:
        from voiceuse.plugins.grok_voice.plugin import GrokVoicePlugin
        return GrokVoicePlugin()
    return None


def list_plugins() -> list[str]:
    """Return a list of available plugin names."""
    return ["grok_voice"]
