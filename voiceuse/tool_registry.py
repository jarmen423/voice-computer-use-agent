"""Shared tool registry and dispatcher for VoiceUse.

This module is the single place where VoiceUse describes the tools an LLM or
realtime voice model may call.  Keeping schema, routing, and adapter logic in
one file prevents the default Brain pipeline and realtime plugins from drifting
apart as the assistant gains more computer-use capabilities.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from voiceuse.models import CommandResult, ToolCall
from voiceuse.os_controller import OSController
from voiceuse.vision_bridge import VisionBridge


TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Launch an application or bring it to foreground if already running.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Name of the application, e.g. 'Codex', 'Chrome'.",
                    }
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": "Find a window by title/substring and bring it to foreground.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Window title substring or application name.",
                    }
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "split_view_apps",
            "description": "Open N instances of an app side-by-side.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Name of the application to open."},
                    "count": {"type": "integer", "description": "Number of instances.", "default": 2},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_search",
            "description": "Open browser, focus address bar, type query/URL, submit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "browser": {"type": "string", "description": "Browser name, optional."},
                    "query": {"type": "string", "description": "Search query or URL to type."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": (
                "Use the computer-use loop to observe the screen, choose UI actions, "
                "execute them, re-observe, and verify that the requested element was clicked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Natural-language description of the UI element or visual task.",
                    },
                    "app_name": {
                        "type": "string",
                        "description": "Optional app/window context to narrow the screen region.",
                    },
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into the currently focused window or a specified app's text input.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type."},
                    "app_name": {"type": "string", "description": "Optional target app name."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_chat",
            "description": "Find a specific chat/conversation in an app's sidebar by label and open it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Application name."},
                    "chat_label": {"type": "string", "description": "Label or title of the chat to open."},
                },
                "required": ["app_name", "chat_label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_system",
            "description": "Execute a raw system command string. Dangerous and always confirmation-gated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Raw shell or terminal command."}
                },
                "required": ["command"],
            },
        },
    },
]


OS_CONTROLLER_TOOLS: set[str] = {
    "open_app",
    "focus_window",
    "split_view_apps",
    "browser_search",
    "type_text",
    "find_chat",
    "execute_system",
}

VISION_TOOLS: set[str] = {"click_element"}


async def dispatch_tool_call(
    tool_call: ToolCall,
    os_controller: OSController,
    vision_bridge: VisionBridge,
) -> CommandResult:
    """Execute one model-requested tool call against the local computer.

    Args:
        tool_call: Parsed LLM/realtime function call.
        os_controller: Local OS automation adapter.
        vision_bridge: Computer-use loop adapter for visual tasks.

    Returns:
        A normalized :class:`CommandResult` so all pipelines can report and test
        execution consistently.
    """
    name = tool_call.tool_name
    params = tool_call.parameters

    if name == "focus_window":
        app_name = str(params.get("app_name", ""))
        window = os_controller.find_window(app_name)
        if window is None:
            return CommandResult(success=False, message=f"No window found matching '{app_name}'")
        result = os_controller.focus_window(window)
        return _normalize_result(result)

    if name == "type_text":
        text = str(params.get("text", ""))
        app_name = params.get("app_name")
        if app_name:
            window = os_controller.find_window(str(app_name))
            if window is not None:
                focus_res = os_controller.focus_window(window)
                normalized = _normalize_result(focus_res)
                if not normalized.success:
                    return CommandResult(
                        success=False,
                        message=f"Failed to focus {app_name}: {normalized.message}",
                    )
        result = os_controller.type_text(text)
        if isinstance(result, CommandResult):
            return result
        target = f"app {app_name}" if app_name else "current focus"
        return CommandResult(success=True, message=f"Typed text into {target}.")

    if name in OS_CONTROLLER_TOOLS:
        method = getattr(os_controller, name, None)
        if method is None:
            return CommandResult(success=False, message=f"OSController does not implement '{name}'")
        result = await method(**params) if asyncio.iscoroutinefunction(method) else method(**params)
        return _normalize_result(result)

    if name == "click_element":
        result = await vision_bridge.find_and_click(
            description=str(params.get("description", "")),
            app_name=_optional_string(params.get("app_name")),
        )
        return _normalize_result(result)

    return CommandResult(success=False, message=f"Unknown tool '{name}'")


def _normalize_result(result: Any) -> CommandResult:
    """Convert mixed legacy tool returns into a CommandResult."""
    if isinstance(result, CommandResult):
        return result
    if result is None:
        return CommandResult(success=True, message="Done.")
    return CommandResult(success=True, message=str(result))


def _optional_string(value: Any) -> Optional[str]:
    """Return a non-empty string or None for optional tool parameters."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None
