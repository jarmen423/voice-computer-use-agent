"""Plugin discovery for VoiceUse.

Plugins live under ``voiceuse/plugins/<name>/plugin.py`` and expose a concrete
``PluginBase`` subclass. The host still asks for one active plugin via
``get_plugin(config)``, but adding a plugin no longer requires editing this
registry file.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import Optional, Type

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
    for plugin_name in list_plugins():
        plugin_config = getattr(config.plugins, plugin_name, None)
        if plugin_config is None or not getattr(plugin_config, "enabled", False):
            continue
        plugin_cls = _load_plugin_class(plugin_name)
        return plugin_cls()
    return None


def list_plugins() -> list[str]:
    """Return plugin package names that contain a ``plugin.py`` module."""
    package_path = Path(__file__).parent
    names: list[str] = []
    for module in pkgutil.iter_modules([str(package_path)]):
        if not module.ispkg or module.name.startswith("_"):
            continue
        if (package_path / module.name / "plugin.py").exists():
            names.append(module.name)
    return sorted(names)


def _load_plugin_class(plugin_name: str) -> Type[PluginBase]:
    """Import a plugin module and return its concrete PluginBase subclass."""
    module = importlib.import_module(f"{__name__}.{plugin_name}.plugin")
    candidates: list[Type[PluginBase]] = []
    for _, value in inspect.getmembers(module, inspect.isclass):
        if value is PluginBase:
            continue
        if issubclass(value, PluginBase) and value.__module__ == module.__name__:
            candidates.append(value)
    if not candidates:
        raise RuntimeError(f"Plugin '{plugin_name}' does not define a PluginBase subclass.")
    return candidates[0]
