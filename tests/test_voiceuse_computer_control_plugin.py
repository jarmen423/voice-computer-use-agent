"""Tests for the repo-local VoiceUse Computer Control Codex plugin."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from voiceuse.models import CommandResult


PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "voiceuse-computer-control"
SERVER_PATH = PLUGIN_ROOT / "scripts" / "mcp_server.py"


def _load_server_module():
    """Load the plugin MCP server as a module for unit tests."""
    spec = importlib.util.spec_from_file_location("voiceuse_mcp_server", SERVER_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeTools:
    """Fake tool backend for JSON-RPC handler tests."""

    def list_windows(self):
        """Return deterministic window metadata."""
        return {"windows": [{"title": "Chrome"}]}

    def click(self, x: int, y: int) -> CommandResult:
        """Return a deterministic click result."""
        return CommandResult(success=True, message=f"Clicked ({x}, {y}).")


def test_plugin_manifest_points_to_mcp_and_skills() -> None:
    """The plugin manifest should expose both MCP tools and guidance skill."""
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "voiceuse-computer-control"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"


def test_mcp_server_lists_voiceuse_tools() -> None:
    """The MCP server should advertise the desktop-control tool surface."""
    server = _load_server_module()
    tool_names = {tool["name"] for tool in server.TOOLS}

    assert "voiceuse_observe_screen" in tool_names
    assert "voiceuse_click" in tool_names
    assert "voiceuse_type_text" in tool_names


def test_mcp_handler_dispatches_tool_call() -> None:
    """JSON-RPC tools/call should dispatch to the named VoiceUse tool."""
    server = _load_server_module()

    response = server.handle_request(
        FakeTools(),
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "voiceuse_click", "arguments": {"x": 10, "y": 20}},
        },
    )

    assert response["id"] == 1
    assert response["result"]["isError"] is False
    assert "Clicked" in response["result"]["content"][0]["text"]
