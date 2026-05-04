"""Health check and dependency validation for VoiceUse.

Performs startup validation to ensure required binaries and Python
packages are available, warning the user early instead of failing
mysteriously at runtime.
"""

import importlib.util
import logging
import shutil
import sys
from dataclasses import dataclass, field
from typing import List

from voiceuse.config import Config

logger = logging.getLogger("voiceuse.health")


@dataclass
class HealthReport:
    """Result of a dependency health check."""

    ok: bool
    missing_packages: List[str] = field(default_factory=list)
    missing_binaries: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def check_installation(config: Config) -> HealthReport:
    """Run a comprehensive dependency check and return a :class:`HealthReport`.

    Inspects:
        * Required Python packages (pyaudio, pynput, etc.).
        * Optional binaries (ffplay, mpv) if TTS is enabled.
        * Platform-specific helpers (pywin32 on Windows, xdotool on Linux).

    Args:
        config: The loaded application configuration.

    Returns:
        A :class:`HealthReport` summarising missing or optional components.
    """
    missing_packages: List[str] = []
    missing_binaries: List[str] = []
    warnings: List[str] = []

    # Core packages
    _REQUIRED = [
        "pyaudio",
        "pynput",
        "groq",
        "yaml",
        "pydantic",
    ]
    _OPTIONAL = [
        ("pvporcupine", "wake-word support"),
        ("webrtcvad", "VAD silence detection"),
        ("edge_tts", "Edge TTS primary engine"),
        ("pyttsx3", "pyttsx3 fallback TTS"),
        ("pygame", "pygame audio playback fallback"),
        ("pydub", "pydub audio playback fallback"),
        ("simpleaudio", "simpleaudio playback fallback"),
        ("anthropic", "Anthropic computer-use provider"),
        ("openai", "OpenAI fallback LLM"),
    ]

    for pkg in _REQUIRED:
        if importlib.util.find_spec(pkg) is None:
            missing_packages.append(pkg)

    for pkg, feature in _OPTIONAL:
        if importlib.util.find_spec(pkg) is None:
            warnings.append(f"{pkg} not installed — {feature} disabled.")

    # TTS playback binaries
    if config.tts.enabled:
        has_ffplay = shutil.which("ffplay") is not None
        has_mpv = shutil.which("mpv") is not None
        if not has_ffplay and not has_mpv:
            warnings.append(
                "Neither ffplay nor mpv found in PATH. TTS will rely on pygame/pydub fallbacks."
            )

    # Platform-specific
    if sys.platform.startswith("win"):
        if importlib.util.find_spec("pywin32") is None and importlib.util.find_spec("win32gui") is None:
            warnings.append("pywin32 not installed — Windows window management may be limited.")
    elif sys.platform.startswith("linux"):
        for binary, purpose in [("xdotool", "window management"), ("wmctrl", "window enumeration")]:
            if shutil.which(binary) is None:
                warnings.append(f"{binary} not in PATH — Linux {purpose} disabled.")

    ok = len(missing_packages) == 0
    return HealthReport(
        ok=ok,
        missing_packages=missing_packages,
        missing_binaries=missing_binaries,
        warnings=warnings,
    )


def print_report(report: HealthReport) -> None:
    """Pretty-print a health report to the console."""
    if report.ok and not report.warnings:
        print("[HealthCheck] All core dependencies satisfied.")
        return

    if report.missing_packages:
        print("[HealthCheck] MISSING required packages:")
        for pkg in report.missing_packages:
            print(f"   - {pkg}")
    if report.missing_binaries:
        print("[HealthCheck] MISSING binaries:")
        for binary in report.missing_binaries:
            print(f"   - {binary}")
    if report.warnings:
        print("[HealthCheck] Warnings:")
        for w in report.warnings:
            print(f"   - {w}")
