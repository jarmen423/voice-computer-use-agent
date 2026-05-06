"""MCP stdio server for VoiceUse desktop control.

This module is the installable entry point for the Codex VoiceUse Computer
Control plugin. Keeping the server inside the ``voiceuse`` package means Codex
can start it through a normal console command from any working directory,
instead of depending on a repo-relative Python script path.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import traceback
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

from voiceuse.config import Config
from voiceuse.models import CommandResult, MonitorInfo, WindowInfo
from voiceuse.os_controller import OSController


class VoiceUseComputerTools:
    """Owns the VoiceUse controller instances used by MCP tool handlers.

    The MCP server is intentionally thin: it translates JSON-RPC requests from
    Codex into calls on the existing VoiceUse OS control layer. This keeps the
    plugin from growing a second desktop-automation implementation.
    """

    def __init__(self) -> None:
        """Load configuration and create the OS controller.

        Side effects:
            Reads ``VOICEUSE_CONFIG`` when set, otherwise falls back to the
            current process' ``config.yaml``. The plugin manifest sets this to
            an absolute repo config path so the globally installed CLI works
            even when Codex starts from another directory.
        """
        config_path = os.environ.get("VOICEUSE_CONFIG", "config.yaml")
        self.config = Config.from_yaml(config_path)
        self.os = OSController(self.config)

    def observe_screen(self, app_name: str | None = None) -> dict[str, Any]:
        """Capture the current screen or a named app window.

        Args:
            app_name: Optional app/window name. When omitted, captures the
                primary monitor.

        Returns:
            Target metadata plus base64-encoded PNG data for the MCP image
            response.

        Raises:
            RuntimeError: If the requested window or monitor cannot be found.
        """
        output_path = Path(tempfile.gettempdir()) / f"voiceuse_mcp_observe_{os.getpid()}_{time.time_ns()}.png"
        if app_name:
            window = self.os.find_window(app_name)
            if window is None:
                raise RuntimeError(f"Could not find window matching '{app_name}'.")
            focus = self.os.focus_window(window)
            if not focus.success:
                raise RuntimeError(focus.message)
            self.os.screenshot_window(window, str(output_path))
            target = {
                "kind": "window",
                "label": window.title,
                "origin_x": window.rect[0],
                "origin_y": window.rect[1],
                "width": window.rect[2],
                "height": window.rect[3],
                "screenshot_path": str(output_path),
            }
        else:
            monitor = self._primary_monitor()
            self.os.screenshot_monitor(monitor.index, str(output_path))
            target = {
                "kind": "monitor",
                "label": monitor.name,
                "origin_x": monitor.rect[0],
                "origin_y": monitor.rect[1],
                "width": monitor.rect[2],
                "height": monitor.rect[3],
                "screenshot_path": str(output_path),
            }
        image_data = base64.b64encode(output_path.read_bytes()).decode("ascii")
        return {"target": target, "image_data": image_data}

    def list_windows(self) -> dict[str, Any]:
        """Return visible desktop windows for planning and verification."""
        return {"windows": [self._window_to_dict(window) for window in self.os.list_windows()]}

    def open_app(self, app_name: str) -> CommandResult:
        """Open or focus an application by name."""
        return self.os.open_app(app_name)

    def focus_window(self, app_name: str) -> CommandResult:
        """Focus the best matching visible window.

        Args:
            app_name: User-facing app/window name such as ``Chrome``.

        Returns:
            Command result describing whether a matching window was focused.
        """
        window = self.os.find_window(app_name)
        if window is None:
            return CommandResult(success=False, message=f"No window found matching '{app_name}'.")
        return self.os.focus_window(window)

    def click(self, x: int, y: int) -> CommandResult:
        """Click absolute desktop coordinates."""
        self.os.click(int(x), int(y))
        return CommandResult(success=True, message=f"Clicked ({x}, {y}).")

    def type_text(self, text: str) -> CommandResult:
        """Type text into the focused control."""
        self.os.type_text(text)
        return CommandResult(success=True, message=f"Typed {len(text)} characters.")

    def press_key(self, key: str) -> CommandResult:
        """Press one keyboard key such as enter, tab, or escape."""
        self.os.press_key(key)
        return CommandResult(success=True, message=f"Pressed {key}.")

    def wait(self, seconds: float = 1.0) -> CommandResult:
        """Wait briefly for UI state to settle after a desktop action."""
        bounded = max(0.0, min(float(seconds), 10.0))
        time.sleep(bounded)
        return CommandResult(success=True, message=f"Waited {bounded:.1f} seconds.")

    def _primary_monitor(self) -> MonitorInfo:
        """Return the primary monitor, or the first monitor if none is marked."""
        monitors = self.os.list_monitors()
        monitor = next((item for item in monitors if item.is_primary), monitors[0] if monitors else None)
        if monitor is None:
            raise RuntimeError("No monitors available.")
        return monitor

    @staticmethod
    def _window_to_dict(window: WindowInfo) -> dict[str, Any]:
        """Convert internal window metadata into MCP-safe JSON data."""
        return {
            "title": window.title,
            "pid": window.pid,
            "rect": window.rect,
            "monitor_index": window.monitor_index,
            "is_active": window.is_active,
        }


TOOLS = [
    {
        "name": "voiceuse_observe_screen",
        "description": "Capture the current primary monitor or a named app window. Returns image content and target metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {"app_name": {"type": "string"}},
        },
    },
    {
        "name": "voiceuse_list_windows",
        "description": "List visible desktop windows with titles, rectangles, and active state.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "voiceuse_open_app",
        "description": "Open or focus an application by name.",
        "inputSchema": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]},
    },
    {
        "name": "voiceuse_focus_window",
        "description": "Focus the best matching window by app/window name.",
        "inputSchema": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]},
    },
    {
        "name": "voiceuse_click",
        "description": "Click absolute desktop coordinates.",
        "inputSchema": {
            "type": "object",
            "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
            "required": ["x", "y"],
        },
    },
    {
        "name": "voiceuse_type_text",
        "description": "Type text into the currently focused control.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "voiceuse_press_key",
        "description": "Press one keyboard key such as enter, tab, escape, or backspace.",
        "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
    },
    {
        "name": "voiceuse_wait",
        "description": "Wait up to 10 seconds for UI state to settle.",
        "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number", "default": 1.0}}},
    },
]


def _server_version() -> str:
    """Return the installed package version for MCP client metadata."""
    try:
        return version("voice-computer-use-agent")
    except PackageNotFoundError:
        return "0.0.0"


def call_tool(tools: VoiceUseComputerTools, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one MCP tool call to the VoiceUse desktop adapter.

    Args:
        tools: Tool backend. Tests can pass a fake backend here.
        name: MCP tool name requested by Codex.
        arguments: JSON arguments from the MCP request.

    Returns:
        MCP ``tools/call`` result content.

    Raises:
        RuntimeError: If Codex asks for an unknown tool.
    """
    handlers: dict[str, str] = {
        "voiceuse_observe_screen": "observe_screen",
        "voiceuse_list_windows": "list_windows",
        "voiceuse_open_app": "open_app",
        "voiceuse_focus_window": "focus_window",
        "voiceuse_click": "click",
        "voiceuse_type_text": "type_text",
        "voiceuse_press_key": "press_key",
        "voiceuse_wait": "wait",
    }
    if name not in handlers:
        raise RuntimeError(f"Unknown tool: {name}")
    handler: Callable[..., Any] = getattr(tools, handlers[name])
    result = handler(**arguments)
    if isinstance(result, CommandResult):
        return {
            "content": [{"type": "text", "text": json.dumps(result.__dict__)}],
            "isError": not result.success,
        }
    if name == "voiceuse_observe_screen":
        target = result["target"]
        return {
            "content": [
                {"type": "text", "text": json.dumps(target)},
                {"type": "image", "data": result["image_data"], "mimeType": "image/png"},
            ]
        }
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


def handle_request(tools: VoiceUseComputerTools, request: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC request from Codex.

    Notifications without an ``id`` intentionally produce no response, which is
    required by JSON-RPC and keeps the server compatible with MCP clients that
    send lifecycle notifications.
    """
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:
        return None
    try:
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "voiceuse-computer-control", "version": _server_version()},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = request.get("params", {})
            result = call_tool(
                tools,
                str(params.get("name", "")),
                dict(params.get("arguments") or {}),
            )
        else:
            raise RuntimeError(f"Unsupported method: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32000,
                "message": str(exc),
                "data": traceback.format_exc(),
            },
        }


def main() -> None:
    """Run the MCP server over stdin/stdout."""
    tools = VoiceUseComputerTools()
    for line in sys.stdin:
        if not line.strip():
            continue
        response = handle_request(tools, json.loads(line))
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
