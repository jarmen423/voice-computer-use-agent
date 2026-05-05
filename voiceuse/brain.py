"""Central orchestrator (Brain) for VoiceUse voice agent.

The Brain receives transcribed voice commands, consults an LLM to plan
tool calls, runs safety checks, dispatches execution to OS / vision
modules, and returns a structured result.
"""

import json
import logging
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from voiceuse.action_audit import ActionAuditLog
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
from voiceuse.retry import async_retry
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
                resp = await self._create_chat_completion(
                    client=client,
                    model=model,
                    messages=messages,
                    tools=tools,
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

    @async_retry(
        max_attempts=3,
        base_delay=0.05,
        max_delay=0.5,
        jitter=False,
        retryable_exceptions=(ConnectionError, TimeoutError, OSError, asyncio.TimeoutError),
    )
    async def _create_chat_completion(
        self,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        max_tokens: int,
    ) -> Any:
        """Call one OpenAI-compatible provider with transient-error retries.

        The provider fallback loop should only move to the next model after the
        current provider has had a real chance to recover from short network
        blips, local DNS hiccups, or SDK timeout errors.
        """
        return await client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            tools=tools,
            tool_choice="auto" if tools else None,
            temperature=temperature,
            max_tokens=max_tokens,
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
        self.audit_log = ActionAuditLog(config)
        self._conversation_history: List[Dict[str, Any]] = []
        self._max_history_messages = 30
        self._max_agent_steps = 3
        self._desktop_context_cache = ""
        self._desktop_context_expires_at = 0.0
        self._desktop_context_ttl_seconds = 2.0

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

        desktop_context = await self._build_desktop_context()
        dynamic_prompt = _SYSTEM_PROMPT
        if desktop_context:
            dynamic_prompt += f"\n\n{desktop_context}"

        command_messages: List[Dict[str, Any]] = [{"role": "user", "content": raw_text}]
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": dynamic_prompt},
            *self._conversation_history,
            *command_messages,
        ]

        final_result = CommandResult(success=False, message="No plan was produced.")
        for step in range(1, self._max_agent_steps + 1):
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

            if not plan.tool_calls:
                final_result = CommandResult(
                    success=True,
                    message=plan.content or final_result.message,
                    data=final_result.data,
                )
                command_messages.append({"role": "assistant", "content": final_result.message})
                self._commit_command_history(command_messages)
                return final_result

            result = await self._execute_plan(raw_text, plan)
            final_result = result
            self._append_plan_result_messages(command_messages, plan, result)
            self._append_plan_result_messages(messages, plan, result)

            if not result.success and "cancelled" in result.message.lower():
                self._commit_command_history(command_messages)
                return result

            logger.info("Agent step %d completed: %s", step, result.message)

        final_result = CommandResult(
            success=final_result.success,
            message=(
                f"Reached the {self._max_agent_steps}-step planning limit. "
                f"Last result: {final_result.message}"
            ),
            data=final_result.data,
        )
        command_messages.append({"role": "assistant", "content": f"Result: {final_result.message}"})
        self._commit_command_history(command_messages)
        return final_result

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
        denied_calls: List[Tuple[ToolCall, str]] = []

        for tc in plan.tool_calls:
            safety_result: SafetyCheckResult = self.safety.check_command(tc, raw_text)
            if not safety_result.is_allowed:
                await self.audit_log.record(
                    source="brain",
                    tool_call=tc,
                    decision="denied",
                    raw_text=raw_text,
                    reason=safety_result.denial_reason,
                )
                denied_calls.append(
                    (tc, safety_result.denial_reason or "Safety policy denied this action.")
                )
                continue
            if safety_result.is_safe:
                await self.audit_log.record(
                    source="brain",
                    tool_call=tc,
                    decision="allowed",
                    raw_text=raw_text,
                )
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
                    await self.audit_log.record(
                        source="brain",
                        tool_call=tc,
                        decision="confirmed",
                        raw_text=raw_text,
                        reason=reason,
                    )
                    confirmed_calls.append(tc)
                else:
                    await self.audit_log.record(
                        source="brain",
                        tool_call=tc,
                        decision="denied",
                        raw_text=raw_text,
                        reason="User declined confirmation.",
                    )
                    logger.info("User declined confirmation for %s", tc.tool_name)

        # If after confirmation we still have no confirmed calls and there were blocked ones
        if not confirmed_calls and denied_calls and not blocked_calls:
            denied_summary = " ".join(reason for _, reason in denied_calls)
            return CommandResult(
                success=False,
                message=denied_summary or "Action denied by safety policy.",
            )

        if not confirmed_calls and blocked_calls:
            return CommandResult(
                success=False,
                message="Action cancelled after confirmation.",
            )

        # 3. Dispatch confirmed calls
        results: List[str] = []
        for tc in confirmed_calls:
            try:
                dispatch_result = await self._dispatch_tool_call(tc)
                await self.audit_log.record(
                    source="brain",
                    tool_call=tc,
                    decision="executed",
                    result=dispatch_result,
                    raw_text=raw_text,
                )
                results.append(dispatch_result.message)
            except Exception as exc:
                logger.exception("Tool dispatch failed for %s", tc.tool_name)
                await self.audit_log.record(
                    source="brain",
                    tool_call=tc,
                    decision="failed",
                    result=CommandResult(success=False, message=str(exc)),
                    raw_text=raw_text,
                    reason=str(exc),
                )
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

    def _append_plan_result_messages(
        self,
        messages: List[Dict[str, Any]],
        plan: LLMResponse,
        result: CommandResult,
    ) -> None:
        """Append an assistant tool plan and local tool results to a message list.

        Args:
            messages: Mutable OpenAI-compatible conversation buffer.
            plan: Model response containing tool calls.
            result: Local execution result produced by the tool dispatcher.
        """
        assistant_tool_calls: List[Dict[str, Any]] = []
        for index, tool_call in enumerate(plan.tool_calls):
            if not tool_call.call_id:
                tool_call.call_id = f"voiceuse_call_{len(messages)}_{index}"
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

        messages.append(
            {
                "role": "assistant",
                "content": plan.content or "I will use local tools to continue.",
                "tool_calls": assistant_tool_calls,
            }
        )
        for tool_call in plan.tool_calls:
            messages.append(
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

        messages.append({"role": "assistant", "content": f"Result: {result.message}"})

    def _commit_command_history(self, command_messages: List[Dict[str, Any]]) -> None:
        """Store a completed command transcript in the rolling conversation window."""
        self._conversation_history.extend(command_messages)
        if len(self._conversation_history) > self._max_history_messages:
            self._conversation_history = self._conversation_history[-self._max_history_messages :]

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
        command_messages: List[Dict[str, Any]] = [{"role": "user", "content": raw_text}]

        if plan.tool_calls:
            self._append_plan_result_messages(command_messages, plan, result)
        else:
            command_messages.append(
                {
                    "role": "assistant",
                    "content": plan.content or result.message,
                }
            )
        self._commit_command_history(command_messages)

    # ------------------------------------------------------------------
    # Desktop context for LLM
    # ------------------------------------------------------------------

    async def _build_desktop_context(self) -> str:
        """Snapshot open windows and app aliases into a prompt fragment.

        This gives the LLM awareness of what's actually running and what
        apps are available, so it can resolve ambiguous names like
        'comment browser' → 'Comet Browser' or 'code' → 'Visual Studio Code'.
        Window enumeration can use platform APIs or subprocesses, so it runs in
        a worker thread instead of blocking the event loop. The final prompt
        fragment is cached briefly because window state rarely changes multiple
        times within the same spoken command, and some platforms enumerate
        windows via subprocesses.
        """
        now = time.monotonic()
        if now < self._desktop_context_expires_at:
            return self._desktop_context_cache

        lines: List[str] = []

        # Open windows
        try:
            windows = await asyncio.to_thread(self.os_controller.list_windows)
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

        context = "\n".join(lines)
        self._desktop_context_cache = context
        self._desktop_context_expires_at = now + self._desktop_context_ttl_seconds
        return context

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch_tool_call(self, tc: ToolCall) -> CommandResult:
        """Route a single ToolCall through the shared tool registry."""
        logger.debug("Dispatching %s with params %s", tc.tool_name, tc.parameters)
        result = await dispatch_tool_call(
            tool_call=tc,
            os_controller=self.os_controller,
            vision_bridge=self.vision_bridge,
        )
        if not result.success:
            raise RuntimeError(result.message)
        return result
