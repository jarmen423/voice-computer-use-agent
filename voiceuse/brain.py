"""Central orchestrator (Brain) for VoiceUse voice agent.

The Brain receives transcribed voice commands, consults an LLM to plan
tool calls, runs safety checks, dispatches execution to OS / vision
modules, and returns a structured result.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from voiceuse.config import Config, LLMConfig
from voiceuse.models import CommandResult, ToolCall, VoiceCommand

try:
    import groq
except ImportError:
    groq = None  # type: ignore

try:
    import openai
except ImportError:
    openai = None  # type: ignore

from voiceuse.safety import SafetyGuard, SafetyCheckResult
from voiceuse.os_controller import OSController
from voiceuse.vision_bridge import VisionBridge

logger = logging.getLogger("voiceuse.brain")


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI-compatible function definitions)
# ---------------------------------------------------------------------------

_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Launch an application or bring it to foreground if already running.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Name of the application, e.g. 'Codex', 'Chrome'.",
                    }
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": (
                "Find a window by title/substring, bring it to foreground, "
                "and if it has an obvious main text input, click it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Window title substring or application name.",
                    }
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "split_view_apps",
            "description": "Open N instances of an app side-by-side.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Name of the application to open.",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of instances (default 2).",
                        "default": 2,
                    },
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_search",
            "description": "Open browser, focus address bar, type query/URL, submit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "browser": {
                        "type": "string",
                        "description": "Browser name (optional, uses default if omitted).",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query or URL to type.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": (
                "Use computer vision to find an element described by the user "
                "and click it. Only use if the user explicitly says 'click ...'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Natural-language description of the element to click.",
                    },
                    "app_name": {
                        "type": "string",
                        "description": "Optional app/window context to narrow the search.",
                    },
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": (
                "Type text into the currently focused window or a specified app's text input."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to type.",
                    },
                    "app_name": {
                        "type": "string",
                        "description": "Optional target app name.",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_chat",
            "description": (
                "Find a specific chat/conversation in an app's sidebar by label and open it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Application name, e.g. 'Discord', 'Slack'.",
                    },
                    "chat_label": {
                        "type": "string",
                        "description": "Label / title of the chat to open.",
                    },
                },
                "required": ["app_name", "chat_label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_system",
            "description": (
                "Execute a raw system command string. HIGHLY DANGEROUS, requires confirmation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Raw shell / terminal command to execute.",
                    }
                },
                "required": ["command"],
            },
        },
    },
]

_SYSTEM_PROMPT = (
    "You are the brain of VoiceUse, a desktop voice-controlled assistant. "
    "Your job is to analyze the user's request and emit one or more tool_calls "
    "to control the operating system (open apps, focus windows, type text, etc.).\n\n"
    "Rules:\n"
    "1. Prefer multiple small tool_calls rather than one big action.\n"
    "2. If the user asks to do something you cannot achieve with the available tools, "
    "   explain briefly and do NOT emit a tool_call.\n"
    "3. For dangerous operations (execute_system) you MUST still emit the tool call; "
    "   a safety layer will ask the user for confirmation.\n"
    "4. Only use 'click_element' if the user explicitly says 'click ...'.\n"
    "5. Return concise reasoning before the tool_calls.\n"
)

# Tools that route to OSController
_OS_CONTROLLER_TOOLS: set[str] = {
    "open_app",
    "focus_window",
    "split_view_apps",
    "browser_search",
    "type_text",
    "find_chat",
    "execute_system",
}

# Tools that route to VisionBridge
_VISION_TOOLS: set[str] = {"click_element"}


# ---------------------------------------------------------------------------
# LLM client wrapper
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    model: str = ""


class _LLMClient:
    """Thin async wrapper around Groq / OpenAI SDKs with identical call semantics."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._groq_client: Any = None
        self._openai_client: Any = None
        self._init_clients()

    def _init_clients(self) -> None:
        # Primary: Groq
        if groq is not None and self.config.api_key:
            self._groq_client = groq.AsyncGroq(api_key=self.config.api_key)
            logger.info("Groq async client initialised.")

        # Fallback: OpenAI
        if openai is not None:
            key = self.config.fallback_api_key or self.config.api_key
            if key:
                self._openai_client = openai.AsyncOpenAI(api_key=key)
                logger.info("OpenAI async client initialised.")

    @property
    def _has_groq(self) -> bool:
        return self._groq_client is not None

    @property
    def _has_openai(self) -> bool:
        return self._openai_client is not None

    async def chat(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Call the LLM and return parsed response."""
        errors: List[str] = []

        # --- Try primary (Groq) ---
        if self._has_groq:
            try:
                resp = await self._groq_client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,  # type: ignore[arg-type]
                    tools=tools,
                    tool_choice="auto" if tools else None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return self._parse_openai_response(resp)
            except Exception as exc:
                err = f"Groq primary call failed: {exc}"
                logger.warning(err)
                errors.append(err)

        # --- Try fallback (OpenAI) ---
        if self._has_openai:
            try:
                resp = await self._openai_client.chat.completions.create(
                    model=self.config.fallback_model,
                    messages=messages,  # type: ignore[arg-type]
                    tools=tools,
                    tool_choice="auto" if tools else None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return self._parse_openai_response(resp)
            except Exception as exc:
                err = f"OpenAI fallback call failed: {exc}"
                logger.warning(err)
                errors.append(err)

        raise LLMError(
            f"All LLM providers failed. Errors: {'; '.join(errors)}"
        )

    @staticmethod
    def _parse_openai_response(raw: Any) -> LLMResponse:
        """Parse an OpenAI-compatible chat-completion response."""
        choice = raw.choices[0]
        msg = choice.message
        content: Optional[str] = getattr(msg, "content", None)
        tool_calls_raw = getattr(msg, "tool_calls", None) or []

        parsed_tools: List[ToolCall] = []
        for tc in tool_calls_raw:
            try:
                fn_name = getattr(getattr(tc, "function", tc), "name", None)
                fn_args = getattr(getattr(tc, "function", tc), "arguments", "{}")
                if fn_name:
                    parsed_tools.append(
                        ToolCall(
                            tool_name=fn_name,
                            parameters=json.loads(fn_args) if isinstance(fn_args, str) else fn_args,
                        )
                    )
            except Exception as exc:
                logger.warning("Failed to parse tool_call: %s", exc)

        return LLMResponse(
            content=content,
            tool_calls=parsed_tools,
            model=getattr(raw, "model", ""),
        )


class LLMError(Exception):
    """Raised when no LLM provider succeeds."""


# ---------------------------------------------------------------------------
# Brain
# ---------------------------------------------------------------------------

class Brain:
    """Central orchestrator that plans, checks safety, and executes commands."""

    def __init__(
        self,
        config: Config,
        safety: SafetyGuard,
        os_controller: OSController,
        vision_bridge: VisionBridge,
        tts_manager: Any,
        get_confirmation_text: Any,
    ) -> None:
        self.config = config
        self.safety = safety
        self.os_controller = os_controller
        self.vision_bridge = vision_bridge
        self.tts_manager = tts_manager
        self.get_confirmation_text = get_confirmation_text
        self.llm = _LLMClient(config.llm)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_command(self, raw_text: str) -> CommandResult:
        """End-to-end pipeline: plan → safety → dispatch → result."""
        logger.info("Brain processing: %r", raw_text)

        # Dry-run shortcut: return a mock plan without calling the LLM
        if self.config.app.dry_run:
            logger.info("[dry-run] Returning mock plan for: %r", raw_text)
            mock_call = ToolCall(tool_name="open_app", parameters={"app_name": "chrome"})
            return await self._execute_plan(raw_text, LLMResponse(
                content="Dry-run mock response.",
                tool_calls=[mock_call],
            ))

        # 1. Build LLM messages
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": raw_text},
        ]

        # 2. LLM planning call
        try:
            plan = await self.llm.chat(
                messages=messages,
                tools=_TOOL_SCHEMAS,
                temperature=self.config.llm.temperature,
                max_tokens=self.config.llm.max_tokens,
            )
        except LLMError as exc:
            logger.error("LLM planning failed: %s", exc)
            return CommandResult(
                success=False,
                message=f"I couldn't reach the language model: {exc}",
            )
        except Exception as exc:
            logger.exception("Unexpected error during LLM planning")
            return CommandResult(
                success=False,
                message=f"Unexpected error while planning: {exc}",
            )

        return await self._execute_plan(raw_text, plan)

    async def _execute_plan(self, raw_text: str, plan: LLMResponse) -> CommandResult:
        """Safety-screen and dispatch a planned set of tool calls."""
        # 1. No tool calls → conversational response
        if not plan.tool_calls:
            reply = plan.content or "I understood, but I don't have a tool for that."
            logger.info("No tool_calls emitted; conversational reply.")
            return CommandResult(success=True, message=reply)

        # 2. Safety screening + optional confirmation
        confirmed_calls: List[ToolCall] = []
        blocked_calls: List[Tuple[ToolCall, str]] = []

        for tc in plan.tool_calls:
            safety_result: SafetyCheckResult = self.safety.check_command(tc, raw_text)
            if safety_result.is_safe:
                confirmed_calls.append(tc)
            else:
                blocked_calls.append(
                    (tc, safety_result.confirmation_prompt or "Safety check blocked this action.")
                )

        # If any call needs confirmation, run the spoken confirmation loop
        if blocked_calls:
            for tc, reason in blocked_calls:
                confirmed = await self.safety.confirm(
                    tts_manager=self.tts_manager,
                    get_confirmation_text=self.get_confirmation_text,
                    confirmation_prompt=reason,
                )
                if confirmed:
                    confirmed_calls.append(tc)
                else:
                    logger.info("User declined confirmation for %s", tc.tool_name)

        # If after confirmation we still have no confirmed calls and there were blocked ones
        if not confirmed_calls and blocked_calls:
            return CommandResult(
                success=False,
                message="Action cancelled after confirmation.",
            )

        # 3. Dispatch confirmed calls
        results: List[str] = []
        for tc in confirmed_calls:
            try:
                result = await self._dispatch_tool_call(tc)
                results.append(result)
            except Exception as exc:
                logger.exception("Tool dispatch failed for %s", tc.tool_name)
                results.append(f"Failed to run {tc.tool_name}: {exc}")

        summary = " ".join(results)
        all_ok = all(not r.startswith("Failed") for r in results)
        return CommandResult(
            success=all_ok,
            message=summary or "Done.",
            data={"executed": [tc.tool_name for tc in confirmed_calls]},
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch_tool_call(self, tc: ToolCall) -> str:
        """Route a single ToolCall to the correct subsystem and execute it."""
        name = tc.tool_name
        params = tc.parameters
        logger.debug("Dispatching %s with params %s", name, params)

        # ----- OSController tools -----
        if name in _OS_CONTROLLER_TOOLS:
            # Adapter: focus_window expects WindowInfo, not app_name string
            if name == "focus_window":
                app_name = params.get("app_name", "")
                window = self.os_controller.find_window(app_name)
                if window is None:
                    raise RuntimeError(f"No window found matching '{app_name}'")
                result = self.os_controller.focus_window(window)
                if isinstance(result, CommandResult):
                    return result.message
                return str(result)

            # Adapter: type_text may include optional app_name — focus first if given
            if name == "type_text":
                text = params.get("text", "")
                app_name = params.get("app_name")
                if app_name:
                    window = self.os_controller.find_window(app_name)
                    if window is not None:
                        focus_res = self.os_controller.focus_window(window)
                        if isinstance(focus_res, CommandResult) and not focus_res.success:
                            return f"Failed to focus {app_name}: {focus_res.message}"
                    else:
                        logger.warning("Window '%s' not found for type_text; typing into current focus.", app_name)
                result = self.os_controller.type_text(text)
                if isinstance(result, CommandResult):
                    return result.message
                return f"Typed text into {'app ' + app_name if app_name else 'current focus'}."



            # Direct dispatch for everything else
            method = getattr(self.os_controller, name, None)
            if method is None:
                raise RuntimeError(f"OSController does not implement '{name}'")

            if asyncio.iscoroutinefunction(method):
                result = await method(**params)
            else:
                result = method(**params)

            if isinstance(result, CommandResult):
                return result.message
            return str(result)

        # ----- VisionBridge tools -----
        if name in _VISION_TOOLS:
            # Adapter: tool name is click_element, VisionBridge method is find_and_click
            if name == "click_element":
                description = params.get("description", "")
                app_name = params.get("app_name")
                result = await self.vision_bridge.find_and_click(
                    description=description,
                    app_name=app_name,
                )
                if isinstance(result, CommandResult):
                    return result.message
                return str(result)

            method = getattr(self.vision_bridge, name, None)
            if method is None:
                raise RuntimeError(f"VisionBridge does not implement '{name}'")

            if asyncio.iscoroutinefunction(method):
                result = await method(**params)
            else:
                result = method(**params)

            if isinstance(result, CommandResult):
                return result.message
            return str(result)

        raise RuntimeError(f"Unknown tool '{name}' — no dispatcher configured.")
