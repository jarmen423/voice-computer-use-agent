"""Tests for plugin discovery and activation."""

from __future__ import annotations

from voiceuse.config import Config
from voiceuse.plugins import get_plugin, list_plugins
from voiceuse.plugins.grok_voice.plugin import GrokVoicePlugin


def test_list_plugins_discovers_grok_voice_package() -> None:
    """Plugin discovery should find package directories with plugin.py."""
    assert "grok_voice" in list_plugins()


def test_get_plugin_returns_enabled_plugin_instance() -> None:
    """Enabled plugin config should instantiate the discovered PluginBase subclass."""
    config = Config()
    config.plugins.grok_voice.enabled = True
    config.plugins.grok_voice.api_key = "xai-test"

    plugin = get_plugin(config)

    assert isinstance(plugin, GrokVoicePlugin)


def test_get_plugin_returns_none_when_all_plugins_disabled() -> None:
    """The default pipeline should run when no plugin config is enabled."""
    assert get_plugin(Config()) is None
