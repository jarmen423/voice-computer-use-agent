"""Append-only audit log for local computer-use actions.

VoiceUse can click, type, open apps, and run a very narrow set of system
commands. Those actions need an operator-readable trail so a user or future
agent can answer: what did the model ask to do, was it allowed, and what
happened after execution?
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from voiceuse.config import Config
from voiceuse.models import CommandResult, ToolCall

logger = logging.getLogger("voiceuse.action_audit")


class ActionAuditLog:
    """Writes normalized tool-action records to a local JSONL file."""

    def __init__(self, config: Config) -> None:
        self._enabled = config.safety.audit_enabled
        self._path = Path(config.safety.audit_log_path).expanduser()

    async def record(
        self,
        *,
        source: str,
        tool_call: ToolCall,
        decision: str,
        result: Optional[CommandResult] = None,
        raw_text: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Append one audit event without blocking the event loop.

        Args:
            source: Pipeline that produced the action, such as ``brain`` or
                ``grok_voice``.
            tool_call: Tool name and model-provided parameters.
            decision: Lifecycle label: ``allowed``, ``confirmed``, ``denied``,
                ``executed``, or ``failed``.
            result: Optional execution result to include after dispatch.
            raw_text: Optional user transcription that led to the tool call.
            reason: Optional safety or error explanation.
        """
        if not self._enabled:
            return

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "tool": tool_call.tool_name,
            "parameters": tool_call.parameters,
            "decision": decision,
            "raw_text": raw_text,
            "reason": reason,
            "result": None if result is None else {
                "success": result.success,
                "message": result.message,
                "data": result.data or {},
            },
        }
        await asyncio.to_thread(self._append_record, record)

    def _append_record(self, record: dict[str, Any]) -> None:
        """Synchronously append a JSON line; called from a worker thread."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("Failed to write action audit record: %s", exc)
