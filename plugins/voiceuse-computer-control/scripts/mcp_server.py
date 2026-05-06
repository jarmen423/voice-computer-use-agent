"""Compatibility wrapper for the installable VoiceUse MCP server.

The real implementation lives in :mod:`voiceuse.computer_control_mcp` so Codex
can start it through the global ``voiceuse-computer-control-mcp`` console
command. This script stays in place for older local plugin registrations that
still point at the repo-relative file.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voiceuse.computer_control_mcp import (  # noqa: E402,F401
    TOOLS,
    VoiceUseComputerTools,
    call_tool,
    handle_request,
    main,
)


if __name__ == "__main__":
    main()
