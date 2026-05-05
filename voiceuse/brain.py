"""Central orchestrator (Brain) for VoiceUse voice agent.

The Brain receives transcribed voice commands, consults an LLM to plan
tool calls, runs safety checks, dispatches execution to OS / vision
modules, and returns a structured result.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from voiceuse.config import Config, LLMConfig
from voiceuse.models import CommandResult, ToolCall

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
from voiceuse.tool_registry import TOOL_SCHEMAS, dispatch_tool_call

logger = logging.getLogger("voiceuse.brain")


_SYSTEM_PROMPT = (
    "You are the brain of VoiceUse, a desktop voice-controlled assistant. "
    "Your job is to analyze the user's request and emit one or more tool_calls "
    "to control the operating system (open apps, focus windows, type text, etc.).\n\n"
    "CRITICAL RULES:\n"
    "1. For EVERY actionable user request (open, focus, type, search, click, etc.) "
    "   you MUST emit the appropriate tool_call(s). Do NOT respond conversationally "
    "   when a tool can fulfill the request.\n"
    "2. Prefer multiple small tool_calls rather than one big action.\n"
    "3. If the user asks to do something you cannot achieve with the available tools, "
    "   explain briefly and do NOT emit a tool_call.\n"
    "4. For dangerous operations (execute_system) you MUST still emit the tool call; "
    "   a safety layer will ask the user for confirmation.\n"
    "5. Only use 'click_element' if the user explicitly says 'click ...'.\n"
    "6. Return concise reasoning before the tool_calls.\n"
    "7. You MUST use the function_call / tool_call mechanism. Do NOT put JSON "
    "   in the text content — use the official tools parameter.\n"
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
    """Thin async wrapper around Groq / Cerebras / OpenAI SDKs with identical call semantics."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._groq_client: Any = None
        self._openai_client: Any = None
        self._cerebras_client: Any = None
        self._init_clients()

    def _init_clients(self) -> None:
        # Groq
        if groq is not None and self.config.api_key:
            self._groq_client = groq.AsyncGroq(api_key=self.config.api_key)
            logger.info("Groq async client initialised.")

        # OpenAI (fallback)
        if openai is not None:
            key = self.config.fallback_api_key or self.config.api_key
            if key:
                self._openai_client = openai.AsyncOpenAI(api_key=key)
                logger.info("OpenAI async client initialised.")

            # Cerebras is OpenAI-compatible
            if self.config.cerebras_api_key:
                self._cerebras_client = openai.AsyncOpenAI(
                    base_url="https://api.cerebras.ai/v1",
                    api_key=self.config.cerebras_api_key,
                )
                logger.info("Cerebras async client initialised.")

    @property
    def _has_groq(self) -> bool:
        return self._groq_client is not None

    @property
    def _has_openai(self) -> bool:
        return self._openai_client is not None

    @property
    def _has_cerebras(self) -> bool:
        return self._cerebras_client is not None

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Call the LLM and return parsed response.

        Tries providers in order: primary → fallback.
        Cerebras uses the OpenAI SDK with a custom base_url.
        """
        errors: List[str] = []

        # Build ordered list of (provider_name, model, client)
        provider_queue: List[tuple[str, str, Any]] = []

        # Primary provider
        primary = self.config.provider
        if primary == "groq" and self._has_groq:
            provider_queue.append((primary, self.config.model, self._groq_client))
        elif primary == "cerebras" and self._has_cerebras:
            provider_queue.append((primary, self.config.model, self._cerebras_client))
        elif primary == "openai" and self._has_openai:
            provider_queue.append((primary, self.config.model, self._openai_client))

        # Fallback provider (skip if same as primary)
        fallback = self.config.fallback_provider
        if fallback and fallback != primary:
            if fallback == "groq" and self._has_groq:
                provider_queue.append((fallback, self.config.fallback_model, self._groq_client))
            elif fallback == "cerebras" and self._has_cerebras:
                provider_queue.append((fallback, self.config.fallback_model, self._cerebras_client))
            elif fallback == "openai" and self._has_openai:
                provider_queue.append((fallback, self.config.fallback_model, self._openai_client))

        for name, model, client in provider_queue:
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    tools=tools,
                    tool_choice="auto" if tools else None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return self._parse_openai_response(resp)
            except Exception as exc:
                err = f"{name} call failed: {exc}"
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
                call_id = getattr(tc, "id", None)
                if fn_name:
                    parsed_tools.append(
                        ToolCall(
                            tool_name=fn_name,
                            parameters=json.loads(fn_args) if isinstance(fn_args, str) else fn_args,
                            call_id=call_id,
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
        self._conversation_history: List[Dict[str, Any]] = []
        self._max_history_messages = 30

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
            plan = LLMResponse(
                content="Dry-run mock response.",
                tool_calls=[mock_call],
            )
            result = await self._execute_plan(raw_text, plan)
            self._record_turn(raw_text, plan, result)
            return result

        # 1. Build LLM messages with desktop context
        desktop_context = self._build_desktop_context()
        dynamic_prompt = _SYSTEM_PROMPT
        if desktop_context:
            dynamic_prompt += f"\n\n{desktop_context}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": dynamic_prompt},
            *self._conversation_history,
            {"role": "user", "content": raw_text},
        ]

        # 2. LLM planning call
        try:
            plan = await self.llm.chat(
                messages=messages,
                tools=TOOL_SCHEMAS,
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

        result = await self._execute_plan(raw_text, plan)
        self._record_turn(raw_text, plan, result)
        return result

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
    # Conversation memory
    # ------------------------------------------------------------------

    def _record_turn(
        self,
        raw_text: str,
        plan: LLMResponse,
        result: CommandResult,
    ) -> None:
        """Persist a compact rolling chat history for follow-up voice turns.

        Voice commands are naturally contextual: a user often says "open
        Chrome" and then follows with "type hello in the search bar."  The LLM
        cannot resolve that second turn if every request is a fresh two-message
        conversation.  This buffer stores the user's utterance, the model's
        tool plan, and the execution result so future turns can refer back to
        what actually happened on the computer.

        Args:
            raw_text: The transcribed user utterance.
            plan: The model response used for this turn.
            result: The local execution result after safety and dispatch.
        """
        self._conversation_history.append({"role": "user", "content": raw_text})

        if plan.tool_calls:
            assistant_tool_calls: List[Dict[str, Any]] = []
            for index, tool_call in enumerate(plan.tool_calls):
                if not tool_call.call_id:
                    tool_call.call_id = f"voiceuse_call_{len(self._conversation_history)}_{index}"
                assistant_tool_calls.append(
                    {
                        "id": tool_call.call_id,
                        "type": "function",
                        "function": {
                            "name": tool_call.tool_name,
                            "arguments": json.dumps(tool_call.parameters),
                        },
                    }
                )

            self._conversation_history.append(
                {
                    "role": "assistant",
                    "content": plan.content or "I will use local tools to complete that.",
                    "tool_calls": assistant_tool_calls,
                }
            )
            for tool_call in plan.tool_calls:
                self._conversation_history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.call_id,
                        "name": tool_call.tool_name,
                        "content": json.dumps(
                            {
                                "success": result.success,
                                "message": result.message,
                                "data": result.data or {},
                            }
                        ),
                    }
                )

            self._conversation_history.append(
                {
                    "role": "assistant",
                    "content": f"Result: {result.message}",
                }
            )
        else:
            self._conversation_history.append(
                {
                    "role": "assistant",
                    "content": plan.content or result.message,
                }
            )

        if len(self._conversation_history) > self._max_history_messages:
            self._conversation_history = self._conversation_history[-self._max_history_messages :]

    # ------------------------------------------------------------------
    # Desktop context for LLM
    # ------------------------------------------------------------------

    def _build_desktop_context(self) -> str:
        """Snapshot open windows and app aliases into a prompt fragment.

        This gives the LLM awareness of what's actually running and what
        apps are available, so it can resolve ambiguous names like
        'comment browser' → 'Comet Browser' or 'code' → 'Visual Studio Code'.
        """
        lines: List[str] = []

        # Open windows
        try:
            windows = self.os_controller.list_windows()
            if windows:
                titles = [w.title for w in windows[:15]]  # cap to avoid token bloat
                lines.append("Currently open windows:")
                for t in titles:
                    lines.append(f"  - {t}")
            else:
                lines.append("No visible windows detected.")
        except Exception as exc:
            logger.debug("Could not list windows for context: %s", exc)
            lines.append("(Window list unavailable)")

        # App aliases
        aliases = self.config.app.aliases
        if aliases:
            lines.append("Known app aliases (nickname → real name):")
            for nick, real in aliases.items():
                lines.append(f"  - {nick} → {real}")

        # Preferred browser hint
        lines.append(
            f"Preferred browser: {self.config.app.preferred_browser}. "
            "Use browser_search for web queries unless the user names a different browser."
        )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch_tool_call(self, tc: ToolCall) -> str:
        """Route a single ToolCall through the shared tool registry."""
        logger.debug("Dispatching %s with params %s", tc.tool_name, tc.parameters)
        result = await dispatch_tool_call(
            tool_call=tc,
            os_controller=self.os_controller,
            vision_bridge=self.vision_bridge,
        )
        if not result.success:
            raise RuntimeError(result.message)
        return result.message
