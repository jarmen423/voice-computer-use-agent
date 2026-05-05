"""Build the VoiceUse Codex computer-use prompt from structured JSON input."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def build_prompt(payload: dict[str, Any]) -> str:
    """Return the prompt sent to `codex exec` for one computer-use step."""
    skill_dir = Path(__file__).resolve().parents[1]
    contract = (skill_dir / "references" / "action-contract.md").read_text(encoding="utf-8")
    history = payload.get("history") or "- none yet"
    task = payload.get("task", "")
    target_label = payload.get("target_label", "screen")
    width = payload.get("width", 0)
    height = payload.get("height", 0)

    return (
        "Use the Codex Computer Use skill.\n"
        "You are the computer-use planner for a local desktop voice assistant.\n"
        f"Task: {task}\n"
        f"Observed region: {target_label}, size {width}x{height}.\n\n"
        "Recent action history:\n"
        f"{history}\n\n"
        f"{contract}\n"
    )


def main() -> None:
    """Read JSON from stdin and write the prompt to stdout."""
    payload = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(build_prompt(payload))


if __name__ == "__main__":
    main()
