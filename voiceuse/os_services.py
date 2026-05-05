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
import difflib
import time
from typing import Any, Callable, Optional

from voiceuse.models import CommandResult, WindowInfo

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

    def hotkey(self, *keys: str) -> None:
        """Press a keyboard chord such as ``ctrl+l``."""
        self._require_pyautogui().hotkey(*keys)

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


class ScreenshotService:
    """Capture monitor or window regions through the MSS screenshot backend."""

    def __init__(self, mss_factory: Optional[Any]) -> None:
        self._mss_factory = mss_factory

    def screenshot_monitor(self, monitor_index: int, output_path: str) -> str:
        """Grab a specific monitor and save it as a PNG."""
        mss_factory = self._require_mss()
        with mss_factory() as sct:
            if monitor_index < 1 or monitor_index >= len(sct.monitors):
                raise ValueError(f"Invalid monitor index {monitor_index}. Available: 1..{len(sct.monitors)-1}")
            mon = sct.monitors[monitor_index]
            screenshot = sct.grab(mon)
            self._write_png(screenshot, output_path)
        logger.info("Screenshot saved to %s (monitor %s)", output_path, monitor_index)
        return output_path

    def screenshot_window(self, window: WindowInfo, output_path: str) -> str:
        """Grab a window rectangle and save it as a PNG."""
        mss_factory = self._require_mss()
        x, y, w, h = window.rect
        region = {"left": x, "top": y, "width": w, "height": h}
        with mss_factory() as sct:
            screenshot = sct.grab(region)
            self._write_png(screenshot, output_path)
        logger.info("Screenshot saved to %s (window %s)", output_path, window.title)
        return output_path

    @staticmethod
    def _write_png(screenshot: Any, output_path: str) -> None:
        """Write an MSS screenshot object to disk via mss.tools."""
        import mss.tools

        mss.tools.to_png(screenshot.rgb, screenshot.size, output=output_path)

    def _require_mss(self) -> Any:
        """Return the MSS factory or raise with operational context."""
        if self._mss_factory is None:
            raise RuntimeError("mss is not installed; cannot take screenshots.")
        return self._mss_factory


class WindowResolver:
    """Resolve spoken app/window names against current desktop windows."""

    def __init__(
        self,
        aliases: dict[str, str],
        list_windows: Callable[[], list[WindowInfo]],
    ) -> None:
        self._aliases = aliases
        self._list_windows = list_windows

    def resolve_app_alias(self, app_name: str) -> str:
        """Return the canonical configured app name for a spoken alias."""
        return self._aliases.get(app_name.lower(), app_name)

    def find_window(self, app_name: str) -> Optional[WindowInfo]:
        """Find the best matching window for a spoken app name.

        Matching proceeds from least surprising to most forgiving: direct
        substring, configured alias, then fuzzy title match. Active windows are
        preferred, followed by larger windows because they are usually the main
        app surface rather than popups or utility panels.
        """
        windows = self._list_windows()
        app_name_lower = app_name.lower()

        direct = [w for w in windows if app_name_lower in w.title.lower()]
        if direct:
            return self._best_window(direct)

        canonical = self.resolve_app_alias(app_name)
        if canonical.lower() != app_name_lower:
            alias_matches = [w for w in windows if canonical.lower() in w.title.lower()]
            if alias_matches:
                return self._best_window(alias_matches)

        close = difflib.get_close_matches(app_name, [w.title for w in windows], n=1, cutoff=0.6)
        if close:
            fuzzy_title = close[0]
            logger.info("Fuzzy-matched '%s' to window title '%s'", app_name, fuzzy_title)
            fuzzy_matches = [w for w in windows if w.title == fuzzy_title]
            if fuzzy_matches:
                return self._best_window(fuzzy_matches)

        return None

    @staticmethod
    def _best_window(windows: list[WindowInfo]) -> WindowInfo:
        """Prefer active windows, then largest visible area."""
        for window in windows:
            if window.is_active:
                return window
        return sorted(windows, key=lambda w: w.rect[2] * w.rect[3], reverse=True)[0]


class BrowserWorkflow:
    """User-facing browser and chat workflows built from smaller OS services."""

    def __init__(
        self,
        preferred_browser: str,
        open_app: Callable[[str], CommandResult],
        find_window: Callable[[str], Optional[WindowInfo]],
        focus_window: Callable[[WindowInfo], CommandResult],
        input_simulator: InputSimulator,
    ) -> None:
        self._preferred_browser = preferred_browser
        self._open_app = open_app
        self._find_window = find_window
        self._focus_window = focus_window
        self._input = input_simulator

    def find_chat(self, app_name: str, chat_label: str) -> CommandResult:
        """Focus an app and enter a chat/search label."""
        window = self._find_window(app_name)
        if window is None:
            return CommandResult(success=False, message=f"App '{app_name}' not found for find_chat.")
        focus_res = self._focus_window(window)
        if not focus_res.success:
            return CommandResult(success=False, message=f"Failed to focus {app_name}: {focus_res.message}")
        self._input.type_text(chat_label)
        self._input.press_key("enter")
        return CommandResult(success=True, message=f"Focused {app_name} and entered '{chat_label}'.")

    def browser_search(self, query: str, browser: Optional[str] = None) -> CommandResult:
        """Open a browser, focus the address bar, type a query or URL, and submit."""
        browser_name = browser or self._preferred_browser
        res = self._open_app(browser_name)
        if not res.success:
            return res

        time.sleep(1.0)
        win = None
        for _ in range(10):
            win = self._find_window(browser_name)
            if win:
                break
            time.sleep(0.5)
        if not win:
            return CommandResult(success=False, message=f"Could not find {browser_name} window.")

        focus_res = self._focus_window(win)
        if not focus_res.success:
            return focus_res

        time.sleep(0.5)
        self._input.hotkey("ctrl", "l")
        time.sleep(0.2)
        self._input.type_text(query)
        if not self._looks_like_url(query):
            self._input.press_key("enter")
        return CommandResult(success=True, message=f"Navigated to: {query}")

    @staticmethod
    def _looks_like_url(query: str) -> bool:
        """Return true when a query looks like a direct URL instead of search text."""
        return "." in query and " " not in query
