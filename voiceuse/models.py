"""Shared data models for VoiceUse."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal
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


@dataclass
class CommandResult:
    """Result of executing a tool call."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


@dataclass
class VoiceCommand:
    """A parsed voice command."""
    raw_text: str
    intent: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_prompt: Optional[str] = None
