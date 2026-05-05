"""Focused operating-system services used by :mod:`voiceuse.os_controller`.

``OSController`` remains the public facade because the rest of VoiceUse already
depends on that interface. The classes in this module keep high-risk side
effects isolated: keyboard/mouse simulation and inspect-only command execution.
That makes each policy easier to test without dragging in window enumeration,
browser workflows, and screenshot code.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from typing import Any, Optional

from voiceuse.models import CommandResult

try:
    import pyperclip
except ImportError:  # pragma: no cover
    pyperclip = None  # type: ignore[assignment]

logger = logging.getLogger("voiceuse.os_services")


class InputSimulator:
    """Small adapter around pyautogui for local keyboard and mouse actions."""

    def __init__(self, pyautogui_module: Optional[Any]) -> None:
        self._pyautogui = pyautogui_module

    def click(self, x: int, y: int) -> None:
        """Click at global screen coordinates."""
        self._require_pyautogui().click(x, y)

    def type_text(self, text: str) -> None:
        """Type or paste text into the currently focused control.

        ``pyautogui.typewrite`` is reliable for ASCII keystrokes but does not
        handle many characters produced by modern speech-to-text, such as
        accented names or non-English text. For non-ASCII text, this service
        uses the clipboard and a paste hotkey so the user gets the literal
        transcription instead of dropped or mangled characters.
        """
        pyautogui_module = self._require_pyautogui()
        if text.isascii():
            pyautogui_module.typewrite(text, interval=0.01)
            return
        self._paste_unicode_text(text, pyautogui_module)

    def press_key(self, key: str) -> None:
        """Press a single key such as ``enter``, ``tab``, or ``esc``."""
        self._require_pyautogui().press(key)

    def _paste_unicode_text(self, text: str, pyautogui_module: Any) -> None:
        """Paste text through the clipboard and restore the old clipboard when possible."""
        if pyperclip is None:
            raise RuntimeError("pyperclip is required to type non-ASCII text safely.")

        previous_clipboard: Optional[str] = None
        try:
            previous_clipboard = pyperclip.paste()
        except Exception as exc:
            logger.debug("Could not read clipboard before paste: %s", exc)

        pyperclip.copy(text)
        pyautogui_module.hotkey("ctrl", "v")

        if previous_clipboard is not None:
            try:
                pyperclip.copy(previous_clipboard)
            except Exception as exc:
                logger.debug("Could not restore clipboard after paste: %s", exc)

    def _require_pyautogui(self) -> Any:
        """Return pyautogui or raise a runtime error with operational context."""
        if self._pyautogui is None:
            raise RuntimeError("pyautogui is not installed; cannot simulate input.")
        return self._pyautogui


class SystemCommandExecutor:
    """Execute only inert, inspect-only commands.

    This service deliberately rejects shell mode, shell metacharacters, and
    interpreters/package managers/build tools. A prefix allow-list is not a
    meaningful sandbox for commands that can execute user-provided code.
    """

    _ALLOWED_COMMANDS: dict[str, set[str]] = {
        "echo": set(),
        "pwd": set(),
        "dir": set(),
        "ls": {"-l", "-la", "-al", "-a"},
        "whoami": set(),
    }

    def execute(self, command: str, allow_shell: bool = False) -> CommandResult:
        """Execute a narrow set of inspect-only commands."""
        if not command or not command.strip():
            return CommandResult(success=False, message="Empty command.")

        cmd_str = command.strip()

        if allow_shell:
            return CommandResult(
                success=False,
                message="Shell execution is disabled. Use a dedicated approved tool for shell workflows.",
            )

        try:
            cmd_parts = shlex.split(cmd_str)
        except ValueError:
            return CommandResult(success=False, message="Could not parse command safely.")
        if not cmd_parts:
            return CommandResult(success=False, message="Empty command after parsing.")

        first_token = os.path.basename(cmd_parts[0]).lower()
        allowed_flags = self._ALLOWED_COMMANDS.get(first_token)
        if allowed_flags is None:
            logger.warning("Command '%s' not in safe inspect-only allow-list; blocking.", first_token)
            return CommandResult(
                success=False,
                message=f"Command '{first_token}' is blocked by the inspect-only command policy.",
            )

        flag_error = self._validate_arguments(first_token, allowed_flags, cmd_parts[1:])
        if flag_error:
            return CommandResult(success=False, message=flag_error)

        logger.warning("Executing system command: %s", cmd_str)
        try:
            proc = subprocess.run(
                cmd_parts,
                shell=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            stdout = proc.stdout.strip()[:200] if proc.stdout else ""
            if proc.returncode == 0:
                msg = f"Command executed. Output: {stdout}" if stdout else "Command executed successfully."
                return CommandResult(success=True, message=msg)
            stderr = proc.stderr.strip()[:200] if proc.stderr else ""
            return CommandResult(
                success=False,
                message=f"Command failed (exit {proc.returncode}): {stderr}",
            )
        except Exception as exc:
            logger.error("execute_system failed: %s", exc)
            return CommandResult(success=False, message=f"Failed to execute command: {exc}")

    @staticmethod
    def _validate_arguments(first_token: str, allowed_flags: set[str], args: list[str]) -> str:
        """Return an error message when an argument violates command policy."""
        for arg in args:
            if any(token in arg for token in (";", "&&", "||", "|", ">", "<", "`", "$(")):
                return "Compound shell syntax is blocked."
            if arg.startswith("-") and arg not in allowed_flags:
                return f"Flag '{arg}' is not allowed for command '{first_token}'."
        return ""
