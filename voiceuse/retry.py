"""Resilience utilities for VoiceUse.

Provides an async retry decorator with exponential backoff for
transient API failures (network timeouts, rate limits, etc.).
"""

import asyncio
import functools
import logging
import random
from typing import Any, Callable, Optional, Tuple, Type

logger = logging.getLogger("voiceuse.retry")


DEFAULT_RETRYABLE_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def async_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: Tuple[Type[BaseException], ...] = DEFAULT_RETRYABLE_EXCEPTIONS,
    on_retry: Optional[Callable[[BaseException, int], Any]] = None,
) -> Callable:
    """Decorator that retries an async function with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts before giving up.
        base_delay: Initial delay between retries (seconds).
        max_delay: Cap on the delay between retries (seconds).
        exponential_base: Multiplier for delay on each subsequent retry.
        jitter: If True, add random jitter (0-1s) to each delay to avoid
            thundering-herd issues.
        retryable_exceptions: Tuple of exception types that should trigger a retry.
        on_retry: Optional callback invoked on each retry with
            ``(exception, attempt_number)``.

    Returns:
        A decorator that wraps the target async function.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Optional[BaseException] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exception = exc
                    if attempt == max_attempts:
                        break
                    delay = min(base_delay * (exponential_base ** (attempt - 1)), max_delay)
                    if jitter:
                        delay += random.random()
                    logger.warning(
                        "Retrying %s in %.2fs after attempt %d/%d (%s: %s)",
                        func.__qualname__,
                        delay,
                        attempt,
                        max_attempts,
                        type(exc).__name__,
                        exc,
                    )
                    if on_retry:
                        try:
                            on_retry(exc, attempt)
                        except Exception:
                            pass
                    await asyncio.sleep(delay)
            # All attempts exhausted — re-raise the last exception
            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator
