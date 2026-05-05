"""Safety Guard module for VoiceUse.

Intercepts tool calls that might be destructive and manages the
confirmation flow before potentially harmful actions are executed.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from voiceuse.config import Config
from voiceuse.models import ToolCall

logger = logging.getLogger(__name__)


@dataclass
class SafetyCheckResult:
    """Result of a safety check on a single ToolCall."""

    is_safe: bool
    requires_confirmation: bool
    confirmation_prompt: str
    is_allowed: bool = True
    denial_reason: str = ""


class SafetyGuard:
    """Intercepts destructive tool calls and manages user confirmation.

    The guard inspects every incoming :class:`ToolCall` against a
    configurable keyword list. If a keyword is found in the parameters
    (or optional raw user text) **or** the tool name is ``execute_system``,
    the call is flagged as destructive and a confirmation prompt is
    generated.

    The async :meth:`confirm` method drives the spoken confirmation loop
    using a caller-supplied callback to capture the user's voice response.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        # Build a case-insensitive lookup set for fast membership tests.
        self._keywords = {
            kw.lower() for kw in config.safety.destructive_keywords
        }
        self._allowed_tools = {tool.lower() for tool in config.safety.allowed_tools}
        self._timeout_seconds = config.safety.confirmation_timeout_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_command(
        self,
        tool_call: ToolCall,
        raw_text: Optional[str] = None,
    ) -> SafetyCheckResult:
        """Inspect a single tool call for destructive intent.

        Args:
            tool_call: The LLM-generated tool call to inspect.
            raw_text: Optional raw transcription of the user's voice command.

        Returns:
            A :class:`SafetyCheckResult` indicating whether the call is safe
            and, if not, the confirmation prompt to present to the user.
        """
        # 1. Enforce the explicit permission list before keyword heuristics.
        if tool_call.tool_name.lower() not in self._allowed_tools:
            reason = f"Tool '{tool_call.tool_name}' is not allowed by safety.allowed_tools."
            logger.warning(reason)
            return SafetyCheckResult(
                is_safe=False,
                requires_confirmation=False,
                confirmation_prompt="",
                is_allowed=False,
                denial_reason=reason,
            )

        # 2. "execute_system" is always treated as destructive regardless of
        #    parameter content because it runs arbitrary shell commands.
        if tool_call.tool_name == "execute_system":
            return self._destructive_result(tool_call)

        # 3. Check every parameter value (stringified) against the keyword list.
        if self._parameters_contain_keyword(tool_call.parameters):
            return self._destructive_result(tool_call)

        # 4. Also check the raw user text if provided.
        if raw_text and self._text_contains_keyword(raw_text):
            return self._destructive_result(tool_call)

        # 5. Safe path.
        return SafetyCheckResult(
            is_safe=True,
            requires_confirmation=False,
            confirmation_prompt="",
        )

    async def confirm(
        self,
        tts_manager,
        get_confirmation_text: Callable[[], Awaitable[str]],
        confirmation_prompt: str,
    ) -> bool:
        """Drive a spoken confirmation loop.

        This method is invoked by the orchestrator (e.g. the Brain) when a
        :class:`SafetyCheckResult` has ``requires_confirmation == True``.

        Flow:
            1. Speak the ``confirmation_prompt`` via ``tts_manager.speak()``.
            2. Await the user's spoken response through
               ``get_confirmation_text``.
            3. Parse the response for affirmative / negative intent.
            4. If the callback does not return within
               ``config.safety.confirmation_timeout_seconds``, announce the
               timeout and treat the action as cancelled.

        Args:
            tts_manager: An object with a ``speak(text: str)`` method (may be
                sync or async). If it is a coroutine, it is awaited.
            get_confirmation_text: An async callable that records audio,
                transcribes it, and returns the user's spoken response text.
            confirmation_prompt: The prompt string to speak to the user.

        Returns:
            ``True`` if the user affirms the action, ``False`` otherwise.
        """
        # Step 1 – speak the prompt.
        try:
            await self._safe_speak(tts_manager, confirmation_prompt)
        except Exception as exc:  # pragma: no cover
            logger.error("TTS failed during confirmation prompt: %s", exc)
            # Continue anyway; the user may still respond visually or via
            # another input channel.

        # Step 2 – capture user response with a timeout.
        user_text: Optional[str] = None
        try:
            user_text = await asyncio.wait_for(
                get_confirmation_text(),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Confirmation timed out after %d seconds", self._timeout_seconds
            )
            await self._safe_speak(
                tts_manager,
                "Confirmation timed out. Cancelling action.",
            )
            return False
        except Exception as exc:
            logger.error("Error while capturing confirmation response: %s", exc)
            await self._safe_speak(
                tts_manager,
                "I didn't catch that. Cancelling action.",
            )
            return False

        # Step 3 – parse response.
        if user_text is None:
            return False

        cleaned = user_text.strip().lower()
        logger.debug("Confirmation response: %r", cleaned)

        # Accept "yes", "yep", "yeah", "sure", "confirm", "go ahead"
        # Reject "no", "nope", "cancel", "don't", "do not"
        affirmative = {"yes", "yep", "yeah", "sure", "confirm", "go ahead"}
        negative = {"no", "nope", "cancel", "don't", "dont", "do not"}

        # Quick exact-match check.
        if cleaned in affirmative:
            return True
        if cleaned in negative:
            return False

        # Substring heuristic: if the response contains "yes" or "yep"
        # and does NOT contain "no", treat as affirmative.
        contains_yes = "yes" in cleaned or "yep" in cleaned or "sure" in cleaned
        contains_no = "no" in cleaned or "nope" in cleaned or "don't" in cleaned

        if contains_yes and not contains_no:
            return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parameters_contain_keyword(self, parameters: dict) -> bool:
        """Return ``True`` if any parameter value matches a destructive keyword."""
        for value in parameters.values():
            text = str(value).lower()
            for keyword in self._keywords:
                if keyword in text:
                    logger.warning(
                        "Destructive keyword %r found in parameter value %r",
                        keyword,
                        value,
                    )
                    return True
        return False

    def _text_contains_keyword(self, text: str) -> bool:
        """Return ``True`` if ``text`` matches a destructive keyword."""
        lowered = text.lower()
        for keyword in self._keywords:
            if keyword in lowered:
                logger.warning(
                    "Destructive keyword %r found in raw text", keyword
                )
                return True
        return False

    @staticmethod
    def _destructive_result(tool_call: ToolCall) -> SafetyCheckResult:
        """Build a :class:`SafetyCheckResult` for a destructive tool call."""
        # Pretty-print parameters to avoid leaking huge nested dicts.
        param_str = " ".join(
            f"{k}={v!r}" for k, v in tool_call.parameters.items()
        )
        if len(param_str) > 200:
            param_str = param_str[:197] + "..."

        prompt = (
            f"You asked me to {tool_call.tool_name}"
            f" with {param_str}. Are you sure? Say yes or no."
        )
        return SafetyCheckResult(
            is_safe=False,
            requires_confirmation=True,
            confirmation_prompt=prompt,
        )

    @staticmethod
    async def _safe_speak(tts_manager, text: str) -> None:
        """Invoke ``tts_manager.speak(text)``, handling both sync and async variants."""
        result = tts_manager.speak(text)
        if asyncio.iscoroutine(result):
            await result
