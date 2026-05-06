"""Tests for VoiceUse external desktop action agent backends."""

from __future__ import annotations

from pathlib import Path

import pytest

from voiceuse.agent_backend import CodexCliRunner, ExternalAgentBackend, ExternalAgentRun
from voiceuse.config import Config


class FakeRunner:
    """Fake external runner that records the prompt it received."""

    def __init__(self, run: ExternalAgentRun) -> None:
        self.run_result = run
        self.prompts: list[str] = []

    async def run(self, prompt: str) -> ExternalAgentRun:
        """Return a preconfigured run result."""
        self.prompts.append(prompt)
        return self.run_result


def test_external_agent_prompt_is_not_product_specific() -> None:
    """The reusable prompt should describe an agent contract, not Codex."""
    backend = ExternalAgentBackend(Config(), runner=FakeRunner(ExternalAgentRun(0, "done", "", "")))

    prompt = backend._build_prompt("open chrome")

    assert "desktop action agent" in prompt
    assert "computer-control tools" in prompt
    assert "Codex" not in prompt


@pytest.mark.asyncio
async def test_external_agent_backend_returns_final_message() -> None:
    """Successful external runs should become speakable CommandResults."""
    runner = FakeRunner(ExternalAgentRun(0, "Opened Chrome.", "", ""))
    backend = ExternalAgentBackend(Config(), runner=runner)

    result = await backend.process_command("open chrome")

    assert result.success is True
    assert result.message == "Opened Chrome."
    assert "open chrome" in runner.prompts[0]


@pytest.mark.asyncio
async def test_external_agent_backend_reports_process_failure() -> None:
    """A failed external process should not look like a completed command."""
    runner = FakeRunner(ExternalAgentRun(1, "", "", "authentication failed"))
    backend = ExternalAgentBackend(Config(), runner=runner)

    result = await backend.process_command("open chrome")

    assert result.success is False
    assert "failed" in result.message
    assert result.data["returncode"] == 1


def test_codex_cli_runner_builds_stdin_command() -> None:
    """Codex runner should send prompts on stdin and write a final message file."""
    config = Config()
    config.agent.command = "codex"
    config.agent.working_directory = "."
    config.agent.model = "gpt-test"
    config.agent.sandbox = "danger-full-access"
    runner = CodexCliRunner(config)

    args = runner._build_args(Path("result.txt"))

    assert args[:2] == ["codex", "exec"]
    assert "--output-last-message" in args
    assert "--model" in args
    assert "gpt-test" in args
    assert "--sandbox" in args
    assert "danger-full-access" in args
    assert args[-1] == "-"
