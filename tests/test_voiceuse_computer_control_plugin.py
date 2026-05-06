"""Tests for the VoiceUse Computer Control Codex plugin and MCP CLI."""

from __future__ import annotations

import json
from pathlib import Path

import voiceuse.computer_control_mcp as server
from voiceuse.models import CommandResult


PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "voiceuse-computer-control"
SERVER_PATH = PLUGIN_ROOT / "scripts" / "mcp_server.py"


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
    tool_names = {tool["name"] for tool in server.TOOLS}

    assert "voiceuse_observe_screen" in tool_names
    assert "voiceuse_click" in tool_names
    assert "voiceuse_type_text" in tool_names


def test_mcp_server_reports_installed_package_version() -> None:
    """MCP metadata should not drift from the packaged VoiceUse version."""
    response = server.handle_request(
        FakeTools(),
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )

    assert response["result"]["serverInfo"]["version"] == server._server_version()


def test_mcp_handler_dispatches_tool_call() -> None:
    """JSON-RPC tools/call should dispatch to the named VoiceUse tool."""
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


def test_plugin_mcp_uses_installable_console_command() -> None:
    """The plugin should be launchable globally through the package CLI."""
    mcp = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    config = mcp["mcpServers"]["voiceuse-computer-control"]

    assert config["command"] == "voiceuse-computer-control-mcp"
    assert config["args"] == []
    assert Path(config["env"]["VOICEUSE_CONFIG"]).is_absolute()


def test_compatibility_script_reexports_packaged_server() -> None:
    """Older registrations that call the script should still find handlers."""
    script = SERVER_PATH.read_text(encoding="utf-8")

    assert "voiceuse.computer_control_mcp" in script
    assert "handle_request" in script
