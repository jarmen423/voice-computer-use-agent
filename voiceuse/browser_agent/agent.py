"""Vision-enabled browser agent loop.

The agent repeatedly asks a vision-capable LLM what to do next, executes the
suggested high-level browser action via :class:`BrowserController`, and reports
progress back to the caller.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

try:
    import openai
except ImportError:
    openai = None  # type: ignore

try:
    import groq
except ImportError:
    groq = None  # type: ignore

from voiceuse.browser_agent.controller import BrowserController
from voiceuse.config import BrowserAgentConfig

logger = logging.getLogger(__name__)


@dataclass
class AgentEvent:
    """One event streamed from the agent to the UI."""

    type: str  # text, screenshot, action, result, error, done
    data: Any = None
    message: str = ""


@dataclass
class StepRecord:
    """Internal record of one observe-think-act iteration."""

    step: int
    action: str
    reasoning: str
    result: str = ""


class BrowserAgent:
    """LLM-driven browser automation agent."""

    TOOLS: List[Dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "goto",
                "description": "Navigate to a URL.",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "Full URL or domain to navigate to."}},
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "click",
                "description": "Click an element by visible text or CSS selector.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "Visible text or CSS selector of the element to click."}
                    },
                    "required": ["target"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "type_text",
                "description": "Click a field and type text into it.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "Visible text, placeholder, or selector of the input field."},
                        "text": {"type": "string", "description": "Text to type."},
                    },
                    "required": ["target", "text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "press",
                "description": "Press a single key.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Key name such as Enter, Tab, Escape, ArrowDown."}
                    },
                    "required": ["key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "scroll",
                "description": "Scroll the page.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "default": "down"},
                        "amount": {"type": "integer", "default": 400},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "wait",
                "description": "Wait briefly for the page to settle.",
                "parameters": {
                    "type": "object",
                    "properties": {"seconds": {"type": "number", "default": 1.0}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "done",
                "description": "Indicate the task is complete and provide a concise summary.",
                "parameters": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "failed",
                "description": "Indicate the task cannot be completed and explain why.",
                "parameters": {
                    "type": "object",
                    "properties": {"reason": {"type": "string"}},
                    "required": ["reason"],
                },
            },
        },
    ]

    SYSTEM_PROMPT = (
        "You are a browser automation agent controlling a real Chrome browser. "
        "You are given the current page URL, title, and a list of interactive elements. "
        "Respond with exactly one JSON object containing one action. "
        "CRITICAL RULES:\n"
        "1. If the current URL is not the site the user asked for, use 'goto' first.\n"
        "2. Only click or type into elements that appear in the provided list.\n"
        "3. Prefer targeting elements by their exact visible text.\n"
        "4. If the task is complete, return action 'done' with a summary.\n"
        "5. If you cannot proceed, return action 'failed' with a reason.\n"
        "6. Do not ask questions; just act."
    )

    def __init__(self, config: BrowserAgentConfig, controller: BrowserController) -> None:
        self.config = config
        self.controller = controller
        self._client: Optional[Any] = None
        self._history: List[StepRecord] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_task(self, goal: str, on_event: Optional[Callable[[AgentEvent], None]] = None) -> str:
        """Run one browser task to completion and return a summary."""
        self._history = []
        if not self.controller.is_connected():
            if on_event:
                on_event(AgentEvent("error", message="Browser is not connected. Launch or attach first."))
            return "Browser is not connected."

        for step in range(1, self.config.max_steps + 1):
            try:
                screenshot_b64 = await self.controller.screenshot()
                page_info = await self.controller.get_page_info()
            except Exception as exc:
                if on_event:
                    on_event(AgentEvent("error", message=f"Failed to capture page state: {exc}"))
                return f"Failed to capture page state: {exc}"

            if on_event:
                on_event(AgentEvent("screenshot", data=screenshot_b64))

            action = await self._ask_model(goal, screenshot_b64, page_info)
            if action is None:
                if on_event:
                    on_event(AgentEvent("error", message="Model returned no action."))
                return "Model returned no action."

            action_name = action.get("name", "failed")
            reasoning = action.get("reasoning", "")
            params = action.get("parameters", {})

            if on_event:
                on_event(AgentEvent("text", message=f"Step {step}: {reasoning or action_name}"))
                on_event(AgentEvent("action", data={"name": action_name, "parameters": params}))

            if action_name == "done":
                summary = params.get("summary", "Task completed.")
                if on_event:
                    on_event(AgentEvent("done", message=summary))
                return summary

            if action_name == "failed":
                reason = params.get("reason", "Unable to complete the task.")
                if on_event:
                    on_event(AgentEvent("error", message=reason))
                return reason

            try:
                result_message = await self._execute_action(action_name, params)
            except Exception as exc:
                result_message = f"Action failed: {exc}"
                logger.exception("Browser action failed")

            self._history.append(StepRecord(step=step, action=action_name, reasoning=reasoning, result=result_message))

            if on_event:
                on_event(AgentEvent("result", data={"name": action_name, "message": result_message}))

        final = f"Reached the step limit ({self.config.max_steps}). Last result: {self._history[-1].result if self._history else 'none'}."
        if on_event:
            on_event(AgentEvent("error", message=final))
        return final

    async def stream_task(self, goal: str) -> AsyncIterator[AgentEvent]:
        """Run a task and yield events as they happen."""
        queue: List[AgentEvent] = []

        def on_event(event: AgentEvent) -> None:
            queue.append(event)

        # Run the task in a background task so we can yield events eagerly.
        import asyncio

        task = asyncio.create_task(self.run_task(goal, on_event))

        while not task.done() or queue:
            if queue:
                yield queue.pop(0)
            else:
                await asyncio.sleep(0.05)

        # Yield any final events produced right at the end
        while queue:
            yield queue.pop(0)

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    async def _ask_model(self, goal: str, screenshot_b64: str, page_info: dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Ask the LLM for the next browser action."""
        provider = self.config.llm_provider

        if provider == "codex":
            return await self._call_codex(goal, screenshot_b64, page_info)

        messages = self._build_messages(goal, screenshot_b64, page_info)
        model = self.config.llm_model
        api_key = self.config.llm_api_key

        if provider == "openai":
            return await self._call_openai(messages, model, api_key)
        if provider == "groq":
            return await self._call_groq(messages, model, api_key)

        return {"name": "failed", "parameters": {"reason": f"Unsupported provider: {provider}"}}

    def _build_messages(
        self, goal: str, screenshot_b64: str, page_info: dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Construct the prompt for the LLM.

        When vision is enabled, the screenshot is sent as an image. Otherwise,
        a text summary of the page DOM/interactive elements is sent.
        """
        history_text = "\n".join(
            f"- step {r.step}: {r.action} -> {r.result}"
            for r in self._history[-6:]
        ) or "- none"

        elements = page_info.get("elements", [])
        element_lines = "\n".join(
            f"- [{i}] <{el.get('tag', '?')}> text={el.get('text', '')!r} type={el.get('type')} selector={el.get('selector')}"
            for i, el in enumerate(elements[:25])
        ) or "- (no interactive elements detected)"

        page_summary = (
            f"Current URL: {page_info.get('url')}\n"
            f"Page title: {page_info.get('title')}\n"
            f"Interactive elements on the page:\n{element_lines}\n\n"
            f"Recent actions:\n{history_text}\n\n"
            f"User goal: {goal}"
        )

        action_schema = (
            "Available actions (return exactly one as JSON):\n"
            '{"action": "goto", "url": "https://example.com"}\n'
            '{"action": "click", "target": "visible text or selector"}\n'
            '{"action": "type_text", "target": "field text or selector", "text": "text to type"}\n'
            '{"action": "press", "key": "Enter"}\n'
            '{"action": "scroll", "direction": "down", "amount": 400}\n'
            '{"action": "wait", "seconds": 1}\n'
            '{"action": "done", "summary": "Task completed successfully."}\n'
            '{"action": "failed", "reason": "Explanation of why the task cannot be completed."}\n'
        )

        if self.config.vision_enabled:
            return [
                {"role": "system", "content": self.SYSTEM_PROMPT + "\n\n" + action_schema},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
                        {"type": "text", "text": page_summary},
                    ],
                },
            ]

        return [
            {
                "role": "system",
                "content": (
                    self.SYSTEM_PROMPT
                    + " You do not receive screenshots. Use the provided list of interactive elements "
                    "and their visible text to decide which element to click or type into. Prefer referring "
                    "to elements by their exact visible text or by the provided selector.\n\n"
                    + action_schema
                ),
            },
            {"role": "user", "content": page_summary},
        ]

    async def _call_openai(
        self, messages: List[Dict[str, Any]], model: str, api_key: Optional[str]
    ) -> Dict[str, Any]:
        if openai is None:
            return {"name": "failed", "parameters": {"reason": "openai package is not installed."}}
        if not api_key:
            return {"name": "failed", "parameters": {"reason": "OpenAI API key is not configured."}}

        client = openai.AsyncOpenAI(api_key=api_key)
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                response_format={"type": "json_object"},
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
        except Exception as exc:
            logger.error("OpenAI API error: %s", exc)
            return {"name": "failed", "parameters": {"reason": f"OpenAI API error: {exc}"}}

        return self._parse_json_response(response)

    async def _call_groq(
        self, messages: List[Dict[str, Any]], model: str, api_key: Optional[str]
    ) -> Dict[str, Any]:
        if groq is None:
            return {"name": "failed", "parameters": {"reason": "groq package is not installed."}}
        if not api_key:
            return {"name": "failed", "parameters": {"reason": "Groq API key is not configured."}}

        client = groq.AsyncGroq(api_key=api_key)
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                response_format={"type": "json_object"},
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
        except Exception as exc:
            logger.error("Groq API error: %s", exc)
            return {"name": "failed", "parameters": {"reason": f"Groq API error: {exc}"}}

        return self._parse_json_response(response)

    async def _call_codex(
        self, goal: str, screenshot_b64: str, page_info: dict[str, Any]
    ) -> Dict[str, Any]:
        """Use the local Codex CLI as a vision-capable action planner."""
        import asyncio
        import os
        import tempfile

        action_schema = (
            "You are a browser automation agent controlling a real Chrome browser on a Linux desktop.\n"
            f"Current URL: {page_info.get('url')}\n"
            f"Page title: {page_info.get('title')}\n"
            f"User goal: {goal}\n\n"
            "Respond with exactly one JSON object containing one action. Available actions:\n"
            '{"action": "goto", "url": "https://example.com"}\n'
            '{"action": "click", "target": "visible text or selector"}\n'
            '{"action": "type_text", "target": "field text or selector", "text": "text to type"}\n'
            '{"action": "press", "key": "Enter"}\n'
            '{"action": "scroll", "direction": "down", "amount": 400}\n'
            '{"action": "wait", "seconds": 1}\n'
            '{"action": "done", "summary": "Task completed successfully."}\n'
            '{"action": "failed", "reason": "Explanation of why the task cannot be completed."}\n'
        )

        # Save the screenshot to a temp file for Codex CLI.
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(base64.b64decode(screenshot_b64))
            screenshot_path = tmp.name

        cmd = [
            "codex",
            "exec",
            "-i", screenshot_path,
            "--dangerously-bypass-approvals-and-sandbox",
            "--ephemeral",
            action_schema,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
        except Exception as exc:
            logger.error("Codex CLI error: %s", exc)
            return {"name": "failed", "parameters": {"reason": f"Codex CLI error: {exc}"}}
        finally:
            try:
                os.unlink(screenshot_path)
            except Exception:
                pass

        output = stdout.decode().strip()
        err = stderr.decode().strip()
        if proc.returncode != 0:
            logger.error("Codex CLI exited %d: %s", proc.returncode, err[:500])
            return {"name": "failed", "parameters": {"reason": f"Codex CLI failed: {err}"}}

        return self._extract_json_from_text(output)

    @staticmethod
    def _extract_json_from_text(text: str) -> Dict[str, Any]:
        """Extract the first JSON object from mixed Codex CLI output."""
        text = text.strip()
        if not text:
            return {"name": "failed", "parameters": {"reason": "Codex returned empty output."}}

        # Try whole string first
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return BrowserAgent._normalize_action_dict(data)
        except json.JSONDecodeError:
            pass

        # Strip markdown fences and try again
        cleaned = text
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return BrowserAgent._normalize_action_dict(data)
        except json.JSONDecodeError:
            pass

        # Scan for first balanced JSON object
        start: Optional[int] = None
        depth = 0
        in_string = False
        escape = False
        for index, char in enumerate(text):
            if start is None:
                if char == "{":
                    start = index
                    depth = 1
                continue
            if escape:
                escape = False
                continue
            if char == "\\" and in_string:
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict):
                            return BrowserAgent._normalize_action_dict(data)
                    except json.JSONDecodeError:
                        start = None
                        depth = 0

        return {"name": "failed", "parameters": {"reason": f"Could not extract JSON from Codex output: {text[:200]}"}}

    @staticmethod
    def _normalize_action_dict(data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a parsed action dict into the internal format."""
        action_name = str(data.get("action", "")).strip().lower()
        if not action_name:
            return {"name": "failed", "parameters": {"reason": "Action JSON missing 'action' field."}}
        parameters = {k: v for k, v in data.items() if k != "action"}
        return {"name": action_name, "parameters": parameters, "reasoning": data.get("reasoning", "")}

    @staticmethod
    def _parse_json_response(response: Any) -> Dict[str, Any]:
        """Parse a JSON-object response into an action dict."""
        try:
            content = response.choices[0].message.content or ""
            data = json.loads(content)
            if not isinstance(data, dict):
                return {"name": "failed", "parameters": {"reason": "Model returned non-object JSON."}}

            action_name = str(data.get("action", "")).strip().lower()
            if not action_name:
                return {"name": "failed", "parameters": {"reason": "Model JSON missing 'action' field."}}

            # Build parameters by removing the action key
            parameters = {k: v for k, v in data.items() if k != "action"}
            return {"name": action_name, "parameters": parameters, "reasoning": data.get("reasoning", "")}
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse model JSON: %s", exc)
            return {"name": "failed", "parameters": {"reason": f"Could not parse model JSON: {exc}"}}
        except Exception as exc:
            logger.error("Failed to parse model response: %s", exc)
            return {"name": "failed", "parameters": {"reason": f"Could not parse model response: {exc}"}}

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    async def _execute_action(self, name: str, params: Dict[str, Any]) -> str:
        if name == "goto":
            return await self.controller.goto(str(params.get("url", "")))
        if name == "click":
            return await self.controller.click(str(params.get("target", "")))
        if name == "type_text":
            return await self.controller.type_text(str(params.get("target", "")), str(params.get("text", "")))
        if name == "press":
            return await self.controller.press(str(params.get("key", "")))
        if name == "scroll":
            return await self.controller.scroll(
                str(params.get("direction", "down")),
                int(params.get("amount", 400)),
            )
        if name == "wait":
            return await self.controller.wait(float(params.get("seconds", 1.0)))
        raise RuntimeError(f"Unknown action: {name}")
