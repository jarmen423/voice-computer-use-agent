"""Shared data models for VoiceUse.

These dataclasses are the small, serializable records passed between the
orchestration layer, tool registry, and operating-system adapters. Keeping this
module focused on records that are still used makes the agent easier to reason
about when future work adds more stateful computer-use behavior.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional
from enum import Enum


class Platform(str, Enum):
    WINDOWS = "windows"
    LINUX = "linux"
    MACOS = "macos"


@dataclass
class WindowInfo:
    """Represents a window found on the system."""
    title: str
    pid: int
    rect: tuple  # (x, y, width, height)
    monitor_index: int
    hwnd: Any = None  # platform-specific handle
    is_active: bool = False


@dataclass
class MonitorInfo:
    """Represents a physical monitor."""
    index: int
    name: str
    rect: tuple  # (x, y, width, height) in global screen space
    is_primary: bool = False


@dataclass
class ToolCall:
    """Represents a tool call from the LLM."""
    tool_name: str
    parameters: Dict[str, Any]
    call_id: Optional[str] = None


@dataclass
class CommandResult:
    """Result of executing a tool call."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None

