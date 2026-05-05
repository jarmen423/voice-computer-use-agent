"""Vision Bridge for VoiceUse — bridge to Computer Use engines (Codex CLI or Anthropic API)."""

import asyncio
import base64
import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from voiceuse.config import Config
from voiceuse.models import CommandResult
from voiceuse.os_controller import OSController

logger = logging.getLogger(__name__)


class VisionBridge:
    """Bridge to Computer Use engines for visual understanding and UI interaction."""

    def __init__(self, config: Config, os_controller: OSController) -> None:
        self.config = config
        self.os = os_controller
        self._anthropic_client: Any = None

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
        """Find a UI element matching *description* and click it.

        Steps:
        1. Focus target window / monitor.
        2. Take a screenshot.
        3. Send to the configured vision provider.
        4. Parse coordinates, translate to global screen space.
        5. Click.
        """
        provider = self.config.computer_use.provider
        threshold = self.config.computer_use.confidence_threshold

        # ------------------------------------------------------------------
        # 1. Determine target and take screenshot
        # ------------------------------------------------------------------
        window: Optional[Any] = None
        monitor: Optional[Any] = None
        screenshot_path = os.path.join(tempfile.gettempdir(), "voiceuse_screenshot.png")

        if app_name:
            window = self.os.find_window(app_name)
            if window is None:
                return CommandResult(
                    success=False,
                    message=f"Could not find window for app: {app_name}",
                )
            focus_res = self.os.focus_window(window)
            if not focus_res.success:
                return focus_res
            time.sleep(0.3)
            self.os.screenshot_window(window, screenshot_path)
            origin_x, origin_y = window.rect[0], window.rect[1]
        else:
            monitors = self.os.list_monitors()
            monitor = next((m for m in monitors if m.is_primary), monitors[0] if monitors else None)
            if monitor is None:
                return CommandResult(success=False, message="No monitors available.")
            self.os.screenshot_monitor(monitor.index, screenshot_path)
            origin_x, origin_y = monitor.rect[0], monitor.rect[1]

        # ------------------------------------------------------------------
        # 2. Call vision provider
        # ------------------------------------------------------------------
        if provider == "codex":
            vision_result = await self._call_codex(description, screenshot_path)
        elif provider == "anthropic":
            width = window.rect[2] if window else monitor.rect[2] if monitor else 1920
            height = window.rect[3] if window else monitor.rect[3] if monitor else 1080
            vision_result = await self._call_anthropic(description, screenshot_path, width, height)
        else:
            return CommandResult(success=False, message=f"Unknown vision provider: {provider}")

        if not vision_result["success"]:
            return CommandResult(success=False, message=vision_result.get("message", "Vision call failed."))

        found = vision_result.get("found", False)
        rel_x = vision_result.get("x", 0)
        rel_y = vision_result.get("y", 0)
        confidence = vision_result.get("confidence", 0.0)
        reasoning = vision_result.get("reasoning", "")

        # ------------------------------------------------------------------
        # 3. Confidence gate
        # ------------------------------------------------------------------
        if not found:
            return CommandResult(
                success=False,
                message=f"Element not found. Reasoning: {reasoning}",
            )
        if confidence < threshold:
            return CommandResult(
                success=False,
                message=(
                    f"Low confidence ({confidence:.2f} < {threshold}). "
                    "I don't want to guess. Please rephrase or click manually."
                ),
            )

        # ------------------------------------------------------------------
        # 4. Translate to global coordinates and click
        # ------------------------------------------------------------------
        global_x = origin_x + rel_x
        global_y = origin_y + rel_y

        logger.info(
            "VisionBridge clicking '%s' at global (%d, %d) [relative (%d, %d), confidence %.2f]",
            description, global_x, global_y, rel_x, rel_y, confidence,
        )
        self.os.click(global_x, global_y)

        return CommandResult(
            success=True,
            message=f"Clicked {description} at ({global_x}, {global_y})",
            data={
                "global_x": global_x,
                "global_y": global_y,
                "relative_x": rel_x,
                "relative_y": rel_y,
                "confidence": confidence,
                "reasoning": reasoning,
            },
        )

    # ------------------------------------------------------------------
    # Codex provider
    # ------------------------------------------------------------------
    async def _call_codex(self, description: str, screenshot_path: str) -> Dict[str, Any]:
        """Call the local Codex CLI with the screenshot and description.

        Codex CLI is invoked in non-interactive ``exec`` mode with the screenshot
        attached via ``-i``.  The prompt explicitly requests a JSON object with
        relative coordinates.  Because Codex does not guarantee machine-readable
        output, we aggressively extract the first JSON object from its response.
        """
        prompt = (
            "You are a computer-vision helper. "
            f"Find the UI element described as: {description}\n\n"
            "Return ONLY a JSON object with exactly these keys:\n"
            "  found (bool)\n"
            "  x (int) — horizontal pixel coordinate relative to the screenshot\n"
            "  y (int) — vertical pixel coordinate relative to the screenshot\n"
            "  confidence (float 0.0-1.0)\n"
            "  reasoning (short string)\n\n"
            "No markdown, no code fences, no extra text."
        )
        cmd = [
            "codex",
            "exec",
            "-i", screenshot_path,
            "--full-auto",
            "--ask-for-approval", "never",
            prompt,
        ]
        logger.info("Running Codex CLI: %s", " ".join(cmd))
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

        return {
            "success": True,
            "found": bool(data.get("found", False)),
            "x": int(data.get("x", 0)),
            "y": int(data.get("y", 0)),
            "confidence": float(data.get("confidence", 0.0)),
            "reasoning": str(data.get("reasoning", "")),
        }

    # ------------------------------------------------------------------
    # Anthropic provider
    # ------------------------------------------------------------------
    async def _call_anthropic(
        self, description: str, screenshot_path: str, width: int, height: int
    ) -> Dict[str, Any]:
        """Call Anthropic API with the computer_20241022 beta tool."""
        try:
            client = self._get_anthropic_client()
        except Exception as exc:
            return {"success": False, "message": str(exc)}

        # Encode image to base64
        try:
            with open(screenshot_path, "rb") as f:
                image_bytes = f.read()
            b64_image = base64.b64encode(image_bytes).decode("utf-8")
        except Exception as exc:
            logger.error("Failed to read screenshot for Anthropic: %s", exc)
            return {"success": False, "message": f"Image read error: {exc}"}

        tools: List[Dict[str, Any]] = [
            {
                "type": "computer_20241022",
                "name": "computer",
                "display_width_px": width,
                "display_height_px": height,
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
                        "text": f"Find the element: {description}",
                    },
                ],
            }
        ]

        logger.debug("Anthropic vision request: %s (%dx%d)", description, width, height)
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
            return {
                "success": True,
                "found": False,
                "x": 0,
                "y": 0,
                "confidence": 0.0,
                "reasoning": full_text,
            }

        # Extract action and coordinates from the first tool_use
        tool = tool_uses[0]
        action = tool.input.get("action", "") if hasattr(tool, "input") else ""
        coords = tool.input.get("coordinate", []) if hasattr(tool, "input") else []

        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            rel_x, rel_y = int(coords[0]), int(coords[1])
        else:
            rel_x, rel_y = 0, 0

        # Anthropic computer tool does not return confidence directly; we infer from action presence
        found = action in ("mouse_move", "left_click", "right_click", "middle_click")
        confidence = 1.0 if found else 0.0

        return {
            "success": True,
            "found": found,
            "x": rel_x,
            "y": rel_y,
            "confidence": confidence,
            "reasoning": f"Anthropic tool action: {action}",
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
        import re
        match = re.search(r"\{[\s\S]*?\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None

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
