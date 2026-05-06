"""Voice command backends for VoiceUse.

VoiceUse has two different responsibilities that should stay separate:

1. Voice interface: hotkeys, wake words, microphone capture, STT, and TTS.
2. Desktop action agent: plan, use computer-control tools, verify, and report.

The original implementation used :class:`voiceuse.brain.Brain` for both
planning and tool execution. This module adds a backend boundary so VoiceUse can
keep the voice interface while delegating desktop work to an external
MCP-capable agent. The first concrete runner uses Codex CLI, but the prompt and
class names intentionally use generic "agent" language so future runners can
reuse the same boundary.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from voiceuse.brain import Brain
from voiceuse.config import Config
from voiceuse.models import CommandResult

logger = logging.getLogger("voiceuse.agent_backend")


class VoiceCommandBackend(Protocol):
    """Planner/executor interface used by the voice pipeline after STT."""

    async def process_command(self, text: str) -> CommandResult:
        """Execute one transcribed voice command and return a speakable result."""


class NativeBrainBackend:
    """Adapter that keeps the original in-process Brain pipeline available."""

    def __init__(self, brain: Brain) -> None:
        self.brain = brain

    async def process_command(self, text: str) -> CommandResult:
        """Route the command through the existing Brain implementation."""
        return await self.brain.process_command(text)


@dataclass
class ExternalAgentRun:
    """Raw result from an external agent process."""

    returncode: int
    final_message: str
    stdout: str
    stderr: str


class ExternalAgentRunner(Protocol):
    """Starts a concrete external desktop action agent."""

    async def run(self, prompt: str) -> ExternalAgentRun:
        """Run the external agent with a prepared prompt."""


class CodexCliRunner:
    """Run an MCP-capable desktop action agent through ``codex exec``.

    This class is intentionally isolated from the prompt-building logic. Codex
    is just the first process runner; the rest of VoiceUse only depends on the
    generic :class:`ExternalAgentRunner` contract.
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    async def run(self, prompt: str) -> ExternalAgentRun:
        """Execute ``codex exec`` and capture its final response.

        Args:
            prompt: Complete task instructions to send on stdin.

        Returns:
            Raw process data plus the final message written by the agent.
        """
        output_path = Path(tempfile.gettempdir()) / f"voiceuse_agent_result_{os.getpid()}_{uuid.uuid4().hex}.txt"
        args = self._build_args(output_path)
        logger.info("Starting external agent runner: %s", " ".join(args[:3]))

        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")),
                timeout=self.config.agent.timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return ExternalAgentRun(
                returncode=124,
                final_message="",
                stdout="",
                stderr=f"External agent timed out after {self.config.agent.timeout_seconds} seconds.",
            )

        final_message = ""
        if output_path.exists():
            final_message = output_path.read_text(encoding="utf-8", errors="replace").strip()
            try:
                output_path.unlink()
            except OSError:
                logger.debug("Could not remove external agent output file: %s", output_path)

        return ExternalAgentRun(
            returncode=process.returncode or 0,
            final_message=final_message,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )

    def _build_args(self, output_path: Path) -> list[str]:
        """Build the command line for the configured external runner."""
        cfg = self.config.agent
        args = [
            cfg.command,
            "exec",
            "--json",
            "--output-last-message",
            str(output_path),
            "--cd",
            str(Path(cfg.working_directory).resolve()),
        ]
        if cfg.skip_git_repo_check:
            args.append("--skip-git-repo-check")
        if cfg.model:
            args.extend(["--model", cfg.model])
        if cfg.sandbox:
            args.extend(["--sandbox", cfg.sandbox])
        args.append("-")
        return args


class ExternalAgentBackend:
    """VoiceUse backend that delegates desktop work to an external agent.

    The backend receives plain text from STT, builds an agent-agnostic task
    prompt, invokes a runner, and normalizes the final response into
    :class:`CommandResult` so the rest of the application can keep speaking
    results through TTS.
    """

    def __init__(self, config: Config, runner: ExternalAgentRunner | None = None) -> None:
        self.config = config
        self.runner = runner or CodexCliRunner(config)

    async def process_command(self, text: str) -> CommandResult:
        """Send one voice command to the external desktop action agent."""
        if self.config.app.dry_run:
            return CommandResult(success=True, message=f"Dry run external agent heard: {text}")

        prompt = self._build_prompt(text)
        run = await self.runner.run(prompt)
        if run.returncode != 0:
            detail = self._tail(run.stderr or run.stdout)
            logger.error("External agent failed with code %s: %s", run.returncode, detail)
            return CommandResult(
                success=False,
                message="The desktop action agent failed before it could finish.",
                data={"returncode": run.returncode, "detail": detail},
            )

        message = run.final_message.strip() or self._extract_last_useful_line(run.stdout)
        if not message:
            message = "The desktop action agent finished, but did not return a summary."
        return CommandResult(success=True, message=self._make_speakable(message), data={"runner": self.config.agent.runner})

    def _build_prompt(self, voice_command: str) -> str:
        """Build the reusable desktop-action-agent prompt.

        The text avoids naming a specific agent product. It describes the
        contract any future MCP-capable runner should follow: use available
        computer-control tools, observe before/after actions, and return a short
        spoken summary.
        """
        return f"""You are the desktop action agent for a voice-controlled computer-use assistant.

The user spoke this command:
{voice_command!r}

Use the available computer-control tools to complete the user's desktop task.
Follow this loop for UI work:
1. Observe the current desktop or target app.
2. Decide the smallest useful next action.
3. Act with window, keyboard, mouse, typing, or waiting tools.
4. Observe again when state may have changed.
5. Continue until the task is complete or blocked.

Operational rules:
- Prefer direct app/window tools for opening, focusing, typing, and key presses.
- Use screenshots before coordinate clicks unless the coordinates are already known.
- Do not ask the user to do manual steps unless the task is blocked.
- Avoid mentioning internal tool names in the final answer.
- Return one short, speakable final summary for the voice assistant to say aloud.
"""

    @staticmethod
    def _extract_last_useful_line(stdout: str) -> str:
        """Return the last non-empty non-JSONL line from process output."""
        for line in reversed(stdout.splitlines()):
            stripped = line.strip()
            if stripped and not stripped.startswith("{"):
                return stripped
        return ""

    @staticmethod
    def _make_speakable(message: str) -> str:
        """Keep external-agent responses short enough for TTS."""
        compact = " ".join(message.split())
        if len(compact) <= 500:
            return compact
        return compact[:497].rstrip() + "..."

    @staticmethod
    def _tail(text: str, limit: int = 1000) -> str:
        """Return a bounded diagnostic tail for logs and CommandResult data."""
        stripped = text.strip()
        return stripped[-limit:] if len(stripped) > limit else stripped
