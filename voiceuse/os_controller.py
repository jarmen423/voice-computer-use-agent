"""OS Controller for VoiceUse — cross-platform window management, screenshots, and input."""

import asyncio
import base64
import difflib
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import pyautogui
except ImportError:
    pyautogui = None  # type: ignore[assignment]

try:
    from mss import mss as MSS
except ImportError:
    MSS = None  # type: ignore[assignment,misc]

from voiceuse.config import Config
from voiceuse.models import CommandResult, MonitorInfo, WindowInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy platform-specific imports (guarded so the module loads on every OS)
# ---------------------------------------------------------------------------
_pygetwindow = None
_win32gui = None
_win32con = None
_xdotool_available: bool = False
_wmctrl_available: bool = False

def _check_platform_deps() -> None:
    global _pygetwindow, _win32gui, _win32con, _xdotool_available, _wmctrl_available
    if sys.platform.startswith("win"):
        try:
            import pygetwindow as _pgw  # type: ignore[import]
            _pygetwindow = _pgw
        except Exception as exc:
            logger.debug("pygetwindow not available: %s", exc)
        try:
            import win32gui as _wg  # type: ignore[import]
            import win32con as _wc  # type: ignore[import]
            _win32gui = _wg
            _win32con = _wc
        except Exception as exc:
            logger.debug("pywin32 not available: %s", exc)
    elif sys.platform.startswith("linux"):
        for binary, flag in [("xdotool", "_xdotool_available"), ("wmctrl", "_wmctrl_available")]:
            try:
                subprocess.run([binary, "--version"], capture_output=True, check=True, timeout=2)
                globals()[flag] = True
            except Exception as exc:
                logger.debug("%s not available: %s", binary, exc)


class OSController:
    """Cross-platform OS automation: window management, screenshots, input simulation."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.platform = self._detect_platform()
        _check_platform_deps()
        if pyautogui is None:
            logger.error("pyautogui is not installed; OS control will fail.")
        else:
            # Keep failsafe enabled so moving mouse to corner aborts runaway scripts
            pyautogui.FAILSAFE = True
        if MSS is None:
            logger.error("mss is not installed; screenshots will fail.")
        logger.info("OSController initialised for platform: %s", self.platform)

    # ------------------------------------------------------------------
    # Platform detection
    # ------------------------------------------------------------------
    @staticmethod
    def _detect_platform() -> str:
        plat = sys.platform
        if plat.startswith("win"):
            return "windows"
        elif plat.startswith("linux"):
            return "linux"
        elif plat == "darwin":
            return "macos"
        return "unknown"

    async def initialize(self) -> None:
        """Any async setup required before using the controller."""
        logger.info("OSController async init complete.")

    # ------------------------------------------------------------------
    # Monitors
    # ------------------------------------------------------------------
    def list_monitors(self) -> List[MonitorInfo]:
        """Return a list of physical monitors.

        mss().monitors[0] is the virtual screen bounding box.
        monitors[1]..[N] are individual screens.
        """
        monitors: List[MonitorInfo] = []
        try:
            with MSS() as sct:
                raw = sct.monitors  # list[dict]  ; 0 == all combined
                # Determine primary monitor by largest overlap with (0,0)
                primary_idx = 1
                for i, mon in enumerate(raw[1:], start=1):
                    rect = (mon["left"], mon["top"], mon["width"], mon["height"])
                    is_primary = mon.get("left", 0) == 0 and mon.get("top", 0) == 0
                    if is_primary:
                        primary_idx = i
                    monitors.append(
                        MonitorInfo(
                            index=i,
                            name=mon.get("name", f"monitor_{i}"),
                            rect=rect,
                            is_primary=is_primary,
                        )
                    )
                # If none marked primary, mark the one at (0,0) or first
                if not any(m.is_primary for m in monitors):
                    for m in monitors:
                        if m.rect[0] == 0 and m.rect[1] == 0:
                            m.is_primary = True
                            break
                    else:
                        if monitors:
                            monitors[0].is_primary = True
        except Exception as exc:
            logger.error("Failed to list monitors: %s", exc)
            # Fallback to single monitor at (0,0,1920,1080)
            monitors = [MonitorInfo(index=1, name="primary", rect=(0, 0, 1920, 1080), is_primary=True)]
        return monitors

    # ------------------------------------------------------------------
    # Windows
    # ------------------------------------------------------------------
    def list_windows(self) -> List[WindowInfo]:
        """Enumerate visible windows and return metadata."""
        if self.platform == "windows":
            return self._list_windows_windows()
        elif self.platform == "linux":
            return self._list_windows_linux()
        elif self.platform == "macos":
            return self._list_windows_macos()
        return []

    def _list_windows_windows(self) -> List[WindowInfo]:
        windows: List[WindowInfo] = []
        monitors = self.list_monitors()

        def _get_monitor_for_rect(x: int, y: int, w: int, h: int) -> int:
            cx, cy = x + w // 2, y + h // 2
            for mon in monitors:
                mx, my, mw, mh = mon.rect
                if mx <= cx < mx + mw and my <= cy < my + mh:
                    return mon.index
            return 1

        if _win32gui is not None:
            def _enum_callback(hwnd: Any, _: Any) -> None:
                if not _win32gui.IsWindowVisible(hwnd):
                    return
                title = _win32gui.GetWindowText(hwnd)
                if not title:
                    return
                rect = _win32gui.GetWindowRect(hwnd)  # (left, top, right, bottom)
                x, y, r, b = rect
                w, h = r - x, b - y
                if w <= 0 or h <= 0:
                    return
                # Get PID
                pid = 0
                try:
                    _, pid = _win32gui.GetWindowThreadProcessId(hwnd)
                except Exception:
                    pass
                # Check active
                is_active = hwnd == _win32gui.GetForegroundWindow()
                mon_idx = _get_monitor_for_rect(x, y, w, h)
                windows.append(
                    WindowInfo(
                        title=title,
                        pid=pid,
                        rect=(x, y, w, h),
                        monitor_index=mon_idx,
                        hwnd=hwnd,
                        is_active=is_active,
                    )
                )
            try:
                _win32gui.EnumWindows(_enum_callback, None)
            except Exception as exc:
                logger.warning("EnumWindows failed: %s", exc)

        # Fallback to pygetwindow if win32gui returned nothing
        if not windows and _pygetwindow is not None:
            try:
                for w in _pygetwindow.getAllWindows():
                    if w.width <= 0 or w.height <= 0:
                        continue
                    if not w.title:
                        continue
                    mon_idx = _get_monitor_for_rect(w.left, w.top, w.width, w.height)
                    windows.append(
                        WindowInfo(
                            title=w.title,
                            pid=0,
                            rect=(w.left, w.top, w.width, w.height),
                            monitor_index=mon_idx,
                            hwnd=w._hWnd if hasattr(w, "_hWnd") else None,
                            is_active=False,
                        )
                    )
            except Exception as exc:
                logger.warning("pygetwindow fallback failed: %s", exc)
        return windows

    def _list_windows_linux(self) -> List[WindowInfo]:
        windows: List[WindowInfo] = []
        monitors = self.list_monitors()
        if not _xdotool_available:
            logger.warning("xdotool not available; cannot list windows on Linux.")
            return windows
        try:
            out = subprocess.check_output(
                ["xdotool", "search", "--onlyvisible", ""], stderr=subprocess.DEVNULL, timeout=10
            )
            ids = out.decode().strip().split()
        except subprocess.CalledProcessError:
            return windows
        except Exception as exc:
            logger.error("xdotool search failed: %s", exc)
            return windows

        for wid_str in ids:
            try:
                wid = int(wid_str)
                title_out = subprocess.check_output(
                    ["xdotool", "getwindowname", str(wid)], stderr=subprocess.DEVNULL, timeout=5
                ).decode().strip()
                if not title_out:
                    continue
                geo_out = subprocess.check_output(
                    ["xdotool", "getwindowgeometry", str(wid)], stderr=subprocess.DEVNULL, timeout=5
                ).decode()
                x, y, w, h = 0, 0, 0, 0
                for line in geo_out.splitlines():
                    line = line.strip()
                    if line.startswith("Position:"):
                        parts = line.split(" ")
                        # e.g. "Position: 100,200"
                        pos_part = parts[1] if len(parts) > 1 else "0,0"
                        xy = pos_part.replace(",", " ").split()
                        if len(xy) >= 2:
                            x, y = int(xy[0]), int(xy[1])
                    elif line.startswith("Geometry:"):
                        parts = line.split(" ")
                        # e.g. "Geometry: 800x600"
                        geom_part = parts[1] if len(parts) > 1 else "0x0"
                        wh = geom_part.split("x")
                        if len(wh) == 2:
                            w, h = int(wh[0]), int(wh[1])
                if w <= 0 or h <= 0:
                    continue
                # Determine active window
                active_out = subprocess.check_output(
                    ["xdotool", "getactivewindow"], stderr=subprocess.DEVNULL, timeout=5
                ).decode().strip()
                is_active = str(wid) == active_out
                # Determine monitor
                cx, cy = x + w // 2, y + h // 2
                mon_idx = 1
                for mon in monitors:
                    mx, my, mw, mh = mon.rect
                    if mx <= cx < mx + mw and my <= cy < my + mh:
                        mon_idx = mon.index
                        break
                windows.append(
                    WindowInfo(
                        title=title_out,
                        pid=0,
                        rect=(x, y, w, h),
                        monitor_index=mon_idx,
                        hwnd=wid,
                        is_active=is_active,
                    )
                )
            except Exception as exc:
                logger.debug("Skipping window %s: %s", wid_str, exc)
                continue
        return windows

    def _list_windows_macos(self) -> List[WindowInfo]:
        windows: List[WindowInfo] = []
        monitors = self.list_monitors()
        # Primary method: AppleScript (does not need external deps)
        script = '''
        tell application "System Events"
            set procList to (get processes whose background only is false)
            set resultList to {}
            repeat with proc in procList
                try
                    set procName to name of proc
                    set winList to windows of proc
                    repeat with win in winList
                        try
                            set winName to name of win
                            set winPos to position of win
                            set winSize to size of win
                            set end of resultList to {procName & "|" & winName, (item 1 of winPos), (item 2 of winPos), (item 1 of winSize), (item 2 of winSize)}
                        end try
                    end repeat
                end try
            end repeat
            return resultList
        end tell
        '''
        try:
            out = subprocess.check_output(
                ["osascript", "-e", script], stderr=subprocess.DEVNULL, timeout=15
            )
            # Output is an AppleScript list of records; parsing is fragile.
            # Fall back to Quartz if available for more reliable parsing.
        except Exception as exc:
            logger.debug("AppleScript window enumeration failed: %s", exc)
            out = b""

        # Try Quartz / CGWindowListCopyWindowInfo
        if not windows:
            try:
                import Quartz  # type: ignore[import]
                cg = Quartz.CGWindowListCopyWindowInfo(
                    Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
                    Quartz.kCGNullWindowID,
                )
                for entry in cg:
                    bounds = entry.get("kCGWindowBounds", {})
                    x = int(bounds.get("X", 0))
                    y = int(bounds.get("Y", 0))
                    w = int(bounds.get("Width", 0))
                    h = int(bounds.get("Height", 0))
                    if w <= 0 or h <= 0:
                        continue
                    title = entry.get("kCGWindowName", "")
                    owner = entry.get("kCGWindowOwnerName", "")
                    if not title and not owner:
                        continue
                    full_title = f"{owner} — {title}" if owner and title else (title or owner)
                    pid = entry.get("kCGWindowOwnerPID", 0)
                    wid = entry.get("kCGWindowNumber", 0)
                    cx, cy = x + w // 2, y + h // 2
                    mon_idx = 1
                    for mon in monitors:
                        mx, my, mw, mh = mon.rect
                        if mx <= cx < mx + mw and my <= cy < my + mh:
                            mon_idx = mon.index
                            break
                    windows.append(
                        WindowInfo(
                            title=full_title,
                            pid=pid,
                            rect=(x, y, w, h),
                            monitor_index=mon_idx,
                            hwnd=wid,
                            is_active=False,
                        )
                    )
            except Exception as exc:
                logger.warning("Quartz window enumeration failed: %s", exc)

        if not windows:
            logger.warning("macOS window enumeration returned no windows. Using basic fallback.")
            # Basic fallback: create a dummy window list so downstream code doesn't crash
            # In a real app you'd use AX APIs or the Accessibility framework.
        return windows

    def _resolve_app_alias(self, app_name: str) -> str:
        """Resolve a spoken app name via config aliases.

        Returns the canonical name (the value from the alias map) if a match
        exists, otherwise returns the original *app_name* unchanged.
        """
        aliases = self.config.app.aliases
        lowered = app_name.lower()
        if lowered in aliases:
            return aliases[lowered]
        return app_name

    def find_window(self, app_name: str) -> Optional[WindowInfo]:
        """Find a window whose title contains *app_name* (case-insensitive).

        Resolution order:
        1. Exact substring match (case-insensitive).
        2. Alias lookup (e.g. "comet" → "Comet Browser").
        3. Fuzzy match (handles STT errors like "comment" vs "comet").

        Preference: 1) active window, 2) largest area, 3) first match.
        """
        windows = self.list_windows()
        app_name_lower = app_name.lower()

        # 1. Exact substring match
        matches = [w for w in windows if app_name_lower in w.title.lower()]
        if matches:
            for w in matches:
                if w.is_active:
                    return w
            return sorted(matches, key=lambda w: w.rect[2] * w.rect[3], reverse=True)[0]

        # 2. Alias lookup
        canonical = self._resolve_app_alias(app_name)
        if canonical.lower() != app_name_lower:
            matches = [w for w in windows if canonical.lower() in w.title.lower()]
            if matches:
                for w in matches:
                    if w.is_active:
                        return w
                return sorted(matches, key=lambda w: w.rect[2] * w.rect[3], reverse=True)[0]

        # 3. Fuzzy match — find the window title most similar to app_name
        all_titles = [w.title for w in windows]
        close = difflib.get_close_matches(app_name, all_titles, n=1, cutoff=0.6)
        if close:
            fuzzy_title = close[0]
            logger.info("Fuzzy-matched '%s' to window title '%s'", app_name, fuzzy_title)
            matches = [w for w in windows if w.title == fuzzy_title]
            if matches:
                for w in matches:
                    if w.is_active:
                        return w
                return sorted(matches, key=lambda w: w.rect[2] * w.rect[3], reverse=True)[0]

        return None

    def focus_window(self, window: WindowInfo) -> CommandResult:
        """Bring *window* to the foreground and click its centre to ensure focus."""
        try:
            if self.platform == "windows":
                if _win32gui is None:
                    return CommandResult(success=False, message="win32gui not available.")
                hwnd = window.hwnd
                if hwnd is None:
                    return CommandResult(success=False, message="Window has no native handle.")

                # Restore if minimised
                if _win32gui.IsIconic(hwnd):
                    _win32gui.ShowWindow(hwnd, _win32con.SW_RESTORE)

                # --- Multi-attempt Windows focus strategy ---
                # Windows restricts which processes can steal foreground.
                # We try increasingly aggressive techniques.
                focused = False
                last_err: Exception = RuntimeError("unknown")

                # Attempt 1: AttachThreadInput + SetForegroundWindow
                try:
                    fg_hwnd = _win32gui.GetForegroundWindow()
                    fg_thread = _win32gui.GetWindowThreadProcessId(fg_hwnd, None)
                    target_thread = _win32gui.GetWindowThreadProcessId(hwnd, None)
                    if fg_thread != target_thread:
                        import ctypes
                        ctypes.windll.user32.AttachThreadInput(fg_thread, target_thread, True)
                        focused = bool(_win32gui.SetForegroundWindow(hwnd))
                        ctypes.windll.user32.AttachThreadInput(fg_thread, target_thread, False)
                    else:
                        focused = bool(_win32gui.SetForegroundWindow(hwnd))
                except Exception as exc:
                    last_err = exc
                    logger.debug("focus_window attempt 1 failed: %s", exc)

                # Attempt 2: BringWindowToTop + SetForegroundWindow
                if not focused:
                    try:
                        _win32gui.BringWindowToTop(hwnd)
                        focused = bool(_win32gui.SetForegroundWindow(hwnd))
                    except Exception as exc:
                        last_err = exc
                        logger.debug("focus_window attempt 2 failed: %s", exc)

                # Attempt 3: SetWindowPos TOPMOST trick (forces focus, then removes TOPMOST)
                if not focused:
                    try:
                        _win32gui.SetWindowPos(
                            hwnd, _win32con.HWND_TOPMOST,
                            0, 0, 0, 0,
                            _win32con.SWP_NOMOVE | _win32con.SWP_NOSIZE | _win32con.SWP_SHOWWINDOW,
                        )
                        _win32gui.SetWindowPos(
                            hwnd, _win32con.HWND_NOTOPMOST,
                            0, 0, 0, 0,
                            _win32con.SWP_NOMOVE | _win32con.SWP_NOSIZE | _win32con.SWP_SHOWWINDOW,
                        )
                        focused = True
                    except Exception as exc:
                        last_err = exc
                        logger.debug("focus_window attempt 3 failed: %s", exc)

                if not focused:
                    logger.error("focus_window all attempts failed. Last error: %s", last_err)
                    return CommandResult(success=False, message=f"Failed to focus window: {last_err}")

            elif self.platform == "linux":
                if not _xdotool_available:
                    return CommandResult(success=False, message="xdotool not available.")
                subprocess.run(
                    ["xdotool", "windowactivate", str(window.hwnd)],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
            elif self.platform == "macos":
                # Extract app name from window title (best-effort)
                app_guess = window.title.split(" — ")[0] if " — " in window.title else window.title
                script = f'tell application "{app_guess}" to activate'
                subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    timeout=10,
                )
            else:
                return CommandResult(success=False, message=f"Unsupported platform: {self.platform}")

            # Small delay then click centre to guarantee focus
            time.sleep(0.3)
            x = window.rect[0] + window.rect[2] // 2
            y = window.rect[1] + window.rect[3] // 2
            pyautogui.click(x, y)
            return CommandResult(success=True, message=f"Focused window: {window.title}")
        except Exception as exc:
            logger.error("focus_window failed: %s", exc)
            return CommandResult(success=False, message=f"Failed to focus window: {exc}")

    # ------------------------------------------------------------------
    # App launching
    # ------------------------------------------------------------------
    def open_app(self, app_name: str) -> CommandResult:
        """Launch *app_name* or focus it if already running.

        Automatically resolves app aliases (e.g. "comet" → "Comet Browser")
        before attempting to find or launch.
        """
        # Resolve aliases first so "comet" finds "Comet Browser"
        resolved = self._resolve_app_alias(app_name)
        if resolved != app_name:
            logger.info("Resolved app alias '%s' → '%s'", app_name, resolved)

        # If already running, just focus
        existing = self.find_window(resolved)
        if existing:
            return self.focus_window(existing)

        try:
            if self.platform == "windows":
                # Use os.startfile which is the native Windows API for opening
                # files/apps via shell associations (Start Menu, shortcuts, etc.).
                # We avoid subprocess + shlex because cmd.exe quoting is fragile
                # and a name like "Codex" in PATH resolves to the CLI executable
                # instead of the desktop app.
                try:
                    os.startfile(app_name)
                except OSError:
                    # If the exact name fails (e.g. no file association),
                    # try the "start" shell command as fallback.
                    subprocess.Popen(
                        ["cmd", "/c", "start", "", app_name],
                        shell=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            elif self.platform == "linux":
                # Try the binary name first; if that fails we could use gtk-launch
                binary = app_name.lower().replace(" ", "-")
                subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif self.platform == "macos":
                subprocess.Popen(["open", "-a", app_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                return CommandResult(success=False, message=f"Unsupported platform: {self.platform}")
            return CommandResult(success=True, message=f"Launched {app_name}")
        except Exception as exc:
            return CommandResult(success=False, message=f"Failed to open {app_name}: {exc}")

    # ------------------------------------------------------------------
    # Screenshots
    # ------------------------------------------------------------------
    def screenshot_monitor(self, monitor_index: int, output_path: str) -> str:
        """Grab a specific monitor and save to *output_path*."""
        if MSS is None:
            raise RuntimeError("mss is not installed; cannot take screenshots.")
        with MSS() as sct:
            # mss indices: 0 = all, 1..N = individual
            if monitor_index < 1 or monitor_index >= len(sct.monitors):
                raise ValueError(f"Invalid monitor index {monitor_index}. Available: 1..{len(sct.monitors)-1}")
            mon = sct.monitors[monitor_index]
            screenshot = sct.grab(mon)
            import mss.tools
            mss.tools.to_png(screenshot.rgb, screenshot.size, output=output_path)
        logger.info("Screenshot saved to %s (monitor %s)", output_path, monitor_index)
        return output_path

    def screenshot_window(self, window: WindowInfo, output_path: str) -> str:
        """Grab the region defined by *window.rect* and save to *output_path*."""
        if MSS is None:
            raise RuntimeError("mss is not installed; cannot take screenshots.")
        x, y, w, h = window.rect
        region = {"left": x, "top": y, "width": w, "height": h}
        with MSS() as sct:
            screenshot = sct.grab(region)
            import mss.tools
            mss.tools.to_png(screenshot.rgb, screenshot.size, output=output_path)
        logger.info("Screenshot saved to %s (window %s)", output_path, window.title)
        return output_path

    # ------------------------------------------------------------------
    # Input simulation
    # ------------------------------------------------------------------
    def click(self, x: int, y: int) -> None:
        """Click at global screen coordinates (x, y)."""
        if pyautogui is None:
            raise RuntimeError("pyautogui is not installed; cannot simulate input.")
        pyautogui.click(x, y)

    def type_text(self, text: str) -> None:
        """Type *text* with a small interval between keystrokes."""
        if pyautogui is None:
            raise RuntimeError("pyautogui is not installed; cannot simulate input.")
        pyautogui.typewrite(text, interval=0.01)

    def press_key(self, key: str) -> None:
        """Press a single key (e.g. 'enter', 'tab', 'esc')."""
        if pyautogui is None:
            raise RuntimeError("pyautogui is not installed; cannot simulate input.")
        pyautogui.press(key)

    # ------------------------------------------------------------------
    # System command execution (moved from Brain for architecture cohesion)
    # ------------------------------------------------------------------
    # Minimal allow-list of safe command prefixes. Anything else requires
    # explicit override (not implemented) or is rejected when shell=True.
    _ALLOWED_COMMANDS: set[str] = {
        "echo", "cat", "ls", "dir", "pwd", "cd", "mkdir", "touch",
        "git", "python", "python3", "node", "npm", "yarn", "pnpm",
        "pip", "pip3", "pytest", "cargo", "go", "rustc",
        "start", "open", "code", "notepad", "calc",
    }

    def execute_system(self, command: str, allow_shell: bool = False) -> CommandResult:
        """Execute a system command with safety checks.

        By default commands are parsed with ``shlex.split()`` and run with
        ``shell=False``.  If ``allow_shell`` is True the raw string is passed
        through (DANGEROUS — only for user-supplied compound commands).
        """
        if not command or not command.strip():
            return CommandResult(success=False, message="Empty command.")

        cmd_str = command.strip()

        # Determine whether to use shell=False or shell=True
        use_shell = allow_shell
        if not use_shell:
            # Try to split safely; if shlex fails, fall back to shell=True
            # but only if the command looks "simple".
            try:
                cmd_parts = shlex.split(cmd_str)
            except ValueError:
                return CommandResult(
                    success=False,
                    message="Could not parse command safely.",
                )
            if not cmd_parts:
                return CommandResult(success=False, message="Empty command after parsing.")
            # Basic prefix check — first token without path
            first_token = os.path.basename(cmd_parts[0]).lower()
            if first_token not in self._ALLOWED_COMMANDS:
                logger.warning("Command '%s' not in allow-list; blocking.", first_token)
                return CommandResult(
                    success=False,
                    message=f"Command '{first_token}' is not in the safety allow-list.",
                )
            cmd_parts = cmd_parts
        else:
            cmd_parts = cmd_str

        logger.warning("Executing system command: %s", cmd_str)
        try:
            proc = subprocess.run(
                cmd_parts,
                shell=use_shell,
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

    # ------------------------------------------------------------------
    # Chat finder (best-effort shim — moved from Brain)
    # ------------------------------------------------------------------
    def find_chat(self, app_name: str, chat_label: str) -> CommandResult:
        """Focus *app_name* and type *chat_label* followed by Enter.

        This is a best-effort shim: many modern apps (Discord, Slack,
        Teams) support a universal search via Ctrl+K.  We focus the app
        and type the label, but we do not verify the chat actually opens.
        """
        window = self.find_window(app_name)
        if window is None:
            return CommandResult(success=False, message=f"App '{app_name}' not found for find_chat.")
        focus_res = self.focus_window(window)
        if isinstance(focus_res, CommandResult) and not focus_res.success:
            return CommandResult(success=False, message=f"Failed to focus {app_name}: {focus_res.message}")
        # Best-effort: type the chat label (many apps support Ctrl+K search)
        self.type_text(chat_label)
        self.press_key("enter")
        return CommandResult(success=True, message=f"Focused {app_name} and entered '{chat_label}'.")

    # ------------------------------------------------------------------
    # Browser
    # ------------------------------------------------------------------
    def browser_search(self, query: str, browser: Optional[str] = None) -> CommandResult:
        """Open *browser* (or preferred_browser from config) and navigate to *query*."""
        browser_name = browser or self.config.app.preferred_browser
        # Launch / focus browser
        res = self.open_app(browser_name)
        if not res.success:
            return res

        # Wait for window
        time.sleep(1.0)
        win = None
        for _ in range(10):
            win = self.find_window(browser_name)
            if win:
                break
            time.sleep(0.5)
        if not win:
            return CommandResult(success=False, message=f"Could not find {browser_name} window.")

        focus_res = self.focus_window(win)
        if not focus_res.success:
            return focus_res

        time.sleep(0.5)

        # Focus address bar: Ctrl+L works in Chrome, Firefox, Edge, Safari
        if pyautogui is None:
            return CommandResult(success=False, message="pyautogui is not installed; cannot simulate browser input.")
        pyautogui.keyDown("ctrl")
        pyautogui.keyDown("l")
        pyautogui.keyUp("l")
        pyautogui.keyUp("ctrl")
        time.sleep(0.2)

        # Determine if query is a URL or search term
        is_url = "." in query and " " not in query
        if is_url:
            self.type_text(query)
        else:
            self.type_text(query)
            self.press_key("enter")
        return CommandResult(success=True, message=f"Navigated to: {query}")

    # ------------------------------------------------------------------
    # Split view
    # ------------------------------------------------------------------
    def split_view_apps(self, app_name: str, count: int = 2) -> CommandResult:
        """Open *app_name* *count* times and tile windows horizontally."""
        # Focus / open first instance
        res = self.open_app(app_name)
        if not res.success:
            return res

        windows: List[WindowInfo] = []
        for _ in range(count - 1):
            # Open additional instances
            if self.platform == "windows":
                try:
                    os.startfile(app_name)
                except OSError:
                    subprocess.Popen(
                        ["cmd", "/c", "start", "", app_name],
                        shell=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            elif self.platform == "linux":
                binary = app_name.lower().replace(" ", "-")
                subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif self.platform == "macos":
                subprocess.Popen(["open", "-na", app_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)

        # Collect windows
        for _ in range(20):
            windows = [w for w in self.list_windows() if app_name.lower() in w.title.lower()]
            if len(windows) >= count:
                break
            time.sleep(0.5)

        if len(windows) < count:
            return CommandResult(
                success=False,
                message=f"Only found {len(windows)} window(s) for {app_name}; needed {count}.",
            )

        # Tile on primary monitor
        monitors = self.list_monitors()
        primary = next((m for m in monitors if m.is_primary), monitors[0] if monitors else None)
        if primary is None:
            return CommandResult(success=False, message="No monitors found.")

        mx, my, mw, mh = primary.rect
        slot_width = mw // count
        for i, win in enumerate(windows[:count]):
            tx = mx + i * slot_width
            ty = my
            tw = slot_width
            th = mh
            if self.platform == "windows":
                if _pygetwindow is not None:
                    try:
                        pg_win = _pygetwindow.getWindowsWithTitle(win.title)
                        if pg_win:
                            pg_win[0].moveTo(tx, ty)
                            pg_win[0].resizeTo(tw, th)
                    except Exception as exc:
                        logger.warning("pygetwindow resize failed: %s", exc)
                elif _win32gui is not None:
                    try:
                        hwnd = win.hwnd
                        _win32gui.SetWindowPos(
                            hwnd,
                            None,
                            tx, ty, tw, th,
                            _win32con.SWP_SHOWWINDOW,
                        )
                    except Exception as exc:
                        logger.warning("win32gui resize failed: %s", exc)
            elif self.platform == "linux":
                if _xdotool_available:
                    subprocess.run(
                        ["xdotool", "windowsize", str(win.hwnd), str(tw), str(th)],
                        capture_output=True,
                        check=False,
                        timeout=5,
                    )
                    subprocess.run(
                        ["xdotool", "windowmove", str(win.hwnd), str(tx), str(ty)],
                        capture_output=True,
                        check=False,
                        timeout=5,
                    )
            elif self.platform == "macos":
                # AppleScript resize / position (best-effort)
                app_guess = win.title.split(" — ")[0] if " — " in win.title else app_name
                script = f'''
                tell application "{app_guess}"
                    activate
                    set bounds of front window to {{{tx}, {ty}, {tx + tw}, {ty + th}}}
                end tell
                '''
                subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    timeout=10,
                )
        return CommandResult(success=True, message=f"Tiled {count} {app_name} window(s).")
