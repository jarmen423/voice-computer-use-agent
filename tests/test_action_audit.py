"""Tests for append-only computer-use action auditing."""

from __future__ import annotations

import json

import pytest

from voiceuse.action_audit import ActionAuditLog
from voiceuse.config import Config
from voiceuse.models import CommandResult, ToolCall


@pytest.mark.asyncio
async def test_action_audit_writes_jsonl_record(tmp_path) -> None:
    """Audit records should capture tool, decision, source, and result."""
    config = Config()
    config.safety.audit_enabled = True
    config.safety.audit_log_path = str(tmp_path / "actions.jsonl")
    audit = ActionAuditLog(config)

    await audit.record(
        source="test",
        tool_call=ToolCall(tool_name="open_app", parameters={"app_name": "chrome"}),
        decision="executed",
        result=CommandResult(success=True, message="opened"),
        raw_text="open chrome",
    )

    lines = (tmp_path / "actions.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert record["source"] == "test"
    assert record["tool"] == "open_app"
    assert record["decision"] == "executed"
    assert record["result"]["success"] is True
