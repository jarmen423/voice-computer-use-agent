"""Vision Bridge for VoiceUse — bridge to Computer Use engines (Codex CLI or Anthropic API)."""

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from voiceuse.config import Config
from voiceuse.models import CommandResult
from voiceuse.os_controller import OSController

logger = logging.getLogger(__name__)


@dataclass
class ComputerUseTarget:
    """Screen region that the computer-use loop is allowed to observe and act on.

    Attributes:
        screenshot_path: Path to the latest screenshot for the target region.
        origin_x: Global desktop X coordinate of the screenshot's top-left pixel.
        origin_y: Global desktop Y coordinate of the screenshot's top-left pixel.
        width: Screenshot width in pixels.
        height: Screenshot height in pixels.
        label: Human-readable region label for logs and model prompts.
    """

    screenshot_path: str
    origin_x: int
    origin_y: int
    width: int
    height: int
    label: str


@dataclass
class ComputerUseStep:
    """One observed action in the closed-loop visual automation flow."""

    step_number: int
    action: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


class VisionBridge:
    """Bridge to Computer Use engines for visual understanding and UI interaction."""

    def __init__(self, config: Config, os_controller: OSController) -> None:
        self.config = config
        self.os = os_controller
        self._anthropic_client: Any = None
        self._screenshot_cache: Dict[str, Tuple[float, ComputerUseTarget]] = {}
        self._screenshot_cache_ttl_seconds = 0.2

    # ------------------------------------------------------------------
    # Anthropic client helper (lazy initialisation)
    # ------------------------------------------------------------------
    def _get_anthropic_client(self) -> Any:
        if self._anthropic_client is None:
            try:
                import anthropic as _anthropic  # type: ignore[import]
                api_key = self.config.computer_use.api_key
                if not api_key:
                    raise RuntimeError("Anthropic API key not configured.")
                self._anthropic_client = _anthropic.Anthropic(api_key=api_key)
            except Exception as exc:
                logger.error("Failed to initialise Anthropic client: %s", exc)
                raise
        return self._anthropic_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def find_and_click(
        self, description: str, app_name: Optional[str] = None
    ) -> CommandResult:
        """Run a bounded observe-act-verify loop for a visual click task.

        The previous implementation asked the model for one coordinate and
        clicked it immediately.  That was brittle because UI state can change
        between the screenshot and the click, and the assistant had no way to
        recover from a popup, loading delay, wrong focus, or low-confidence
        target.  This method now treats visual work as a small computer-use
        episode:

        1. Capture the target window or primary monitor.
        2. Ask the configured provider for the next UI action.
        3. Execute only supported local actions.
        4. Capture again so the provider can verify or recover.
        5. Stop when the provider returns ``done`` or the step budget is used.

        Args:
            description: Natural-language UI element or visual task to perform.
            app_name: Optional app/window name used to focus and crop the target.

        Returns:
            CommandResult with the final status and per-step trace data.
        """
        max_steps = 5
        steps: List[ComputerUseStep] = []
        try:
            target = await asyncio.to_thread(self._capture_target, app_name)
        except Exception as exc:
            return CommandResult(success=False, message=str(exc))

        for step_number in range(1, max_steps + 1):
            action = await self._call_computer_use_provider(
                task=description,
                target=target,
                history=steps,
            )

            if not action.get("success", False):
                return CommandResult(
                    success=False,
                    message=action.get("message", "Computer-use provider failed."),
                    data={"steps": [step.__dict__ for step in steps]},
                )

            action_name = str(action.get("action", "failed")).lower()
            message = str(action.get("message") or action.get("reasoning") or action_name)
            step = ComputerUseStep(step_number=step_number, action=action_name, message=message, data=action)
            steps.append(step)

            if action_name == "done":
                return CommandResult(
                    success=True,
                    message=message or f"Completed visual task: {description}",
                    data={"steps": [s.__dict__ for s in steps]},
                )

            if action_name == "failed":
                return CommandResult(
                    success=False,
                    message=message or f"Could not complete visual task: {description}",
                    data={"steps": [s.__dict__ for s in steps]},
                )

            execute_result = await self._execute_computer_action(action, target)
            steps.append(
                ComputerUseStep(
                    step_number=step_number,
                    action=f"executed_{action_name}",
                    message=execute_result.message,
                    data=execute_result.data or {},
                )
            )
            if not execute_result.success:
                return CommandResult(
                    success=False,
                    message=execute_result.message,
                    data={"steps": [s.__dict__ for s in steps]},
                )

            await asyncio.sleep(float(action.get("wait_seconds", 0.4)))
            try:
                target = await asyncio.to_thread(self._capture_target, app_name, True)
            except Exception as exc:
                return CommandResult(
                    success=False,
                    message=str(exc),
                    data={"steps": [s.__dict__ for s in steps]},
                )

        return CommandResult(
            success=False,
            message=f"Computer-use loop reached {max_steps} steps without verified completion.",
            data={"steps": [s.__dict__ for s in steps]},
        )

    def _capture_target(self, app_name: Optional[str], force: bool = False) -> ComputerUseTarget:
        """Focus the requested target and capture the latest observable UI state.

        Args:
            app_name: Optional target app/window name to focus and crop.
            force: When true, bypass the very short cache. The computer-use loop
                uses this after local actions so the next model call sees fresh
                state instead of a pre-action screenshot.
        """
        cache_key = f"app:{app_name}" if app_name else "monitor:primary"
        cached = self._screenshot_cache.get(cache_key)
        now = time.monotonic()
        if not force and cached is not None and cached[0] > now:
            return cached[1]

        screenshot_path = self._new_screenshot_path()

        if app_name:
            window = self.os.find_window(app_name)
            if window is None:
                raise RuntimeError(f"Could not find window for app: {app_name}")
            focus_res = self.os.focus_window(window)
            if not focus_res.success:
                raise RuntimeError(focus_res.message)
            time.sleep(0.3)
            self.os.screenshot_window(window, screenshot_path)
            target = ComputerUseTarget(
                screenshot_path=screenshot_path,
                origin_x=window.rect[0],
                origin_y=window.rect[1],
                width=window.rect[2],
                height=window.rect[3],
                label=f"window:{window.title}",
            )
            self._screenshot_cache[cache_key] = (now + self._screenshot_cache_ttl_seconds, target)
            return target

        monitors = self.os.list_monitors()
        monitor = next((m for m in monitors if m.is_primary), monitors[0] if monitors else None)
        if monitor is None:
            raise RuntimeError("No monitors available.")
        self.os.screenshot_monitor(monitor.index, screenshot_path)
        target = ComputerUseTarget(
            screenshot_path=screenshot_path,
            origin_x=monitor.rect[0],
            origin_y=monitor.rect[1],
            width=monitor.rect[2],
            height=monitor.rect[3],
            label=f"monitor:{monitor.index}",
        )
        self._screenshot_cache[cache_key] = (now + self._screenshot_cache_ttl_seconds, target)
        return target

    @staticmethod
    def _new_screenshot_path() -> str:
        """Return a unique temp path for one computer-use screenshot."""
        filename = f"voiceuse_computer_use_{os.getpid()}_{uuid.uuid4().hex}.png"
        return os.path.join(tempfile.gettempdir(), filename)

    async def _call_computer_use_provider(
        self,
        task: str,
        target: ComputerUseTarget,
        history: List[ComputerUseStep],
    ) -> Dict[str, Any]:
        """Ask the configured provider for the next computer-use action."""
        provider = self.config.computer_use.provider
        if provider == "codex":
            return await self._call_codex_action(task, target, history)
        if provider == "anthropic":
            return await self._call_anthropic_action(task, target, history)
        return {"success": False, "message": f"Unknown vision provider: {provider}"}

    async def _execute_computer_action(
        self,
        action: Dict[str, Any],
        target: ComputerUseTarget,
    ) -> CommandResult:
        """Execute one provider-selected UI action in local screen coordinates."""
        action_name = str(action.get("action", "")).lower()

        if action_name == "click":
            confidence = float(action.get("confidence", 0.0))
            threshold = self.config.computer_use.confidence_threshold
            if confidence < threshold:
                return CommandResult(
                    success=False,
                    message=f"Low click confidence ({confidence:.2f} < {threshold}).",
                    data={"confidence": confidence},
                )
            rel_x = int(action.get("x", 0))
            rel_y = int(action.get("y", 0))
            global_x = target.origin_x + rel_x
            global_y = target.origin_y + rel_y
            logger.info("Computer-use click at global (%d, %d)", global_x, global_y)
            await asyncio.to_thread(self.os.click, global_x, global_y)
            return CommandResult(
                success=True,
                message=f"Clicked at ({global_x}, {global_y}).",
                data={"global_x": global_x, "global_y": global_y, "relative_x": rel_x, "relative_y": rel_y},
            )

        if action_name == "type":
            text = str(action.get("text", ""))
            await asyncio.to_thread(self.os.type_text, text)
            return CommandResult(success=True, message=f"Typed {len(text)} characters.")

        if action_name == "key":
            key = str(action.get("key", ""))
            if not key:
                return CommandResult(success=False, message="Key action missing key.")
            await asyncio.to_thread(self.os.press_key, key)
            return CommandResult(success=True, message=f"Pressed {key}.")

        if action_name == "wait":
            wait_seconds = float(action.get("wait_seconds", 1.0))
            await asyncio.sleep(max(0.0, min(wait_seconds, 5.0)))
            return CommandResult(success=True, message=f"Waited {wait_seconds:.1f} seconds.")

        return CommandResult(success=False, message=f"Unsupported computer-use action: {action_name}")

    # ------------------------------------------------------------------
    # Codex provider
    # ------------------------------------------------------------------
    async def _call_codex_action(
        self,
        task: str,
        target: ComputerUseTarget,
        history: List[ComputerUseStep],
    ) -> Dict[str, Any]:
        """Ask Codex CLI for the next action in the computer-use loop.

        Codex receives the latest screenshot plus a compact trace of actions
        already attempted.  It must return a single JSON action.  The caller
        executes that action, captures a new screenshot, and asks again until
        Codex returns ``done`` or ``failed``.
        """
        history_text = self._format_computer_use_history(history)
        prompt = (
            "You are the computer-use planner for a local desktop voice assistant.\n"
            f"Task: {task}\n"
            f"Observed region: {target.label}, size {target.width}x{target.height}.\n\n"
            "Recent action history:\n"
            f"{history_text}\n\n"
            "Return ONLY one JSON object for the next step. No markdown.\n"
            "Allowed action shapes:\n"
            '{"success": true, "action": "click", "x": 123, "y": 456, '
            '"confidence": 0.91, "message": "why this click should work"}\n'
            '{"success": true, "action": "type", "text": "hello", "message": "why typing is next"}\n'
            '{"success": true, "action": "key", "key": "enter", "message": "why this key is next"}\n'
            '{"success": true, "action": "wait", "wait_seconds": 1.0, "message": "why waiting helps"}\n'
            '{"success": true, "action": "done", "message": "what changed on screen"}\n'
            '{"success": true, "action": "failed", "message": "why the task cannot continue safely"}\n\n'
            "Coordinates must be relative to the screenshot. Do not guess low-confidence clicks. "
            "After an action was executed, inspect the current screenshot and return done only "
            "if the task appears complete."
        )
        cmd = [
            "codex",
            "exec",
            "-i", target.screenshot_path,
            "--full-auto",
            "--ask-for-approval", "never",
            prompt,
        ]
        logger.info("Running Codex CLI: %s", " ".join(cmd))
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.error("Codex CLI timed out after 60 s")
            if proc:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return {"success": False, "message": "Codex CLI timed out."}
        except Exception as exc:
            logger.error("Codex CLI spawn error: %s", exc)
            return {"success": False, "message": f"Codex CLI error: {exc}"}

        out = stdout.decode().strip() if stdout else ""
        err = stderr.decode().strip() if stderr else ""

        if proc.returncode != 0:
            logger.error("Codex CLI exited %d: stderr=%s stdout=%s", proc.returncode, err[:500], out[:500])
            return {"success": False, "message": f"Codex CLI failed (code {proc.returncode}): {err}"}

        logger.debug("Codex CLI stdout: %s", out[:1000])

        # Aggressively extract the first JSON object from the output
        data = self._extract_json_from_text(out)
        if data is None:
            logger.error("Could not extract JSON from Codex output. Raw: %s", out[:1000])
            return {"success": False, "message": f"Could not parse Codex output: {out[:500]}"}

        return self._normalize_computer_action(data)

    # ------------------------------------------------------------------
    # Anthropic provider
    # ------------------------------------------------------------------
    async def _call_anthropic_action(
        self,
        task: str,
        target: ComputerUseTarget,
        history: List[ComputerUseStep],
    ) -> Dict[str, Any]:
        """Ask Anthropic computer-use for the next local UI action."""
        try:
            client = self._get_anthropic_client()
        except Exception as exc:
            return {"success": False, "message": str(exc)}

        # Encode image to base64
        try:
            with open(target.screenshot_path, "rb") as f:
                image_bytes = f.read()
            b64_image = base64.b64encode(image_bytes).decode("utf-8")
        except Exception as exc:
            logger.error("Failed to read screenshot for Anthropic: %s", exc)
            return {"success": False, "message": f"Image read error: {exc}"}

        tools: List[Dict[str, Any]] = [
            {
                "type": "computer_20241022",
                "name": "computer",
                "display_width_px": target.width,
                "display_height_px": target.height,
            }
        ]

        messages: List[Dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Task: {task}\n"
                            f"Recent action history:\n{self._format_computer_use_history(history)}\n\n"
                            "Return the next computer action. If the task is already complete, say so in text."
                        ),
                    },
                ],
            }
        ]

        logger.debug("Anthropic computer-use request: %s (%dx%d)", task, target.width, target.height)
        try:
            # Run synchronous SDK call in thread pool
            response = await asyncio.to_thread(
                client.messages.create,
                model=self.config.computer_use.model,
                max_tokens=1024,
                tools=tools,
                messages=messages,
            )
        except Exception as exc:
            logger.error("Anthropic API error: %s", exc)
            return {"success": False, "message": f"Anthropic API error: {exc}"}

        # Parse tool_use blocks for action/coordinate
        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if not tool_uses:
            # Fallback: look for text content that might contain coordinates
            text_blocks = [block for block in response.content if block.type == "text"]
            full_text = " ".join(b.text for b in text_blocks)
            logger.warning("No tool_use from Anthropic; text: %s", full_text[:300])
            lowered = full_text.lower()
            if "done" in lowered or "complete" in lowered or "already" in lowered:
                return {"success": True, "action": "done", "message": full_text}
            return {
                "success": True,
                "action": "failed",
                "message": full_text or "Anthropic did not return a computer action.",
            }

        # Extract action and coordinates from the first tool_use
        tool = tool_uses[0]
        action = tool.input.get("action", "") if hasattr(tool, "input") else ""
        coords = tool.input.get("coordinate", []) if hasattr(tool, "input") else []

        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            rel_x, rel_y = int(coords[0]), int(coords[1])
        else:
            rel_x, rel_y = 0, 0

        if action in ("left_click", "mouse_move"):
            return {
                "success": True,
                "action": "click" if action == "left_click" else "wait",
                "x": rel_x,
                "y": rel_y,
                "confidence": 1.0,
                "message": f"Anthropic tool action: {action}",
            }

        return {
            "success": True,
            "action": "failed",
            "message": f"Unsupported Anthropic computer action: {action}",
        }

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
        """Aggressively extract the first JSON object from mixed text/markdown.

        Tries (in order):
        1. Parse the whole string as JSON.
        2. Parse after stripping markdown fences.
        3. Use regex to grab the first ``{...}`` block and parse it.
        """
        text = text.strip()
        if not text:
            return None

        # 1. Whole string
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. Strip fences
        cleaned = VisionBridge._strip_code_fences(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 3. Regex grab first { ... } block (naïve but effective for simple objects)
        match = re.search(r"\{[\s\S]*?\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _format_computer_use_history(history: List[ComputerUseStep]) -> str:
        """Render prior loop steps as compact context for the next model call."""
        if not history:
            return "- none yet"
        lines: List[str] = []
        for step in history[-8:]:
            lines.append(f"- step {step.step_number}: {step.action}: {step.message}")
        return "\n".join(lines)

    @staticmethod
    def _normalize_computer_action(data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize provider JSON into the local computer-use action contract."""
        if not isinstance(data, dict):
            return {"success": False, "message": "Provider returned non-object JSON."}

        action = str(data.get("action", "")).lower().strip()
        if not action:
            # Backward compatibility for older coordinate-only prompts.
            if data.get("found") is True:
                action = "click"
            else:
                action = "failed"

        normalized = dict(data)
        normalized["success"] = bool(data.get("success", True))
        normalized["action"] = action
        normalized["message"] = str(
            data.get("message")
            or data.get("reasoning")
            or ("Provider selected next action." if normalized["success"] else "Provider failed.")
        )

        if action == "click":
            normalized["x"] = int(data.get("x", 0))
            normalized["y"] = int(data.get("y", 0))
            normalized["confidence"] = float(data.get("confidence", 0.0))
        elif action == "wait":
            normalized["wait_seconds"] = float(data.get("wait_seconds", 1.0))
        elif action == "type":
            normalized["text"] = str(data.get("text", ""))
        elif action == "key":
            normalized["key"] = str(data.get("key", ""))

        return normalized

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Remove markdown code fences from a string."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove opening fence
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            # Remove closing fence
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text
