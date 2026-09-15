from __future__ import annotations

import random
import time
from typing import Callable, Optional, TypeVar

from .config import RetryConfig
from .exceptions import TrackingError

T = TypeVar("T")


def classify_http_error(status_code: int, message: str, retry_after: Optional[float] = None) -> TrackingError:
    from .exceptions import (
        AuthenticationError,
        PermissionDenied,
        ProviderServerError,
        RateLimitError,
        TransientNetworkError,
    )

    if status_code in {401, 403} and "permission" in message.lower():
        return PermissionDenied(message)
    if status_code in {401, 403}:
        return AuthenticationError(message)
    if status_code == 429:
        return RateLimitError(message, retry_after_seconds=retry_after)
    if status_code >= 500:
        return ProviderServerError(message)
    if status_code in {408, 409, 425}:
        return TransientNetworkError(message)
    return TrackingError(message)


def retry_delay(attempt: int, config: RetryConfig, retry_after: Optional[float] = None) -> float:
    if retry_after is not None:
        return min(float(retry_after), config.max_delay_seconds)
    delay = config.base_delay_seconds * (2 ** max(0, attempt - 1))
    delay = min(delay, config.max_delay_seconds)
    if config.jitter:
        delay = delay * (0.5 + random.random())
    return delay


def call_with_retry(
    fn: Callable[[], T],
    config: RetryConfig,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    last_error: Optional[Exception] = None
    attempts = max(1, config.max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except TrackingError as exc:
            last_error = exc
            if not exc.retryable or attempt >= attempts:
                raise
            wait = retry_delay(
                attempt,
                config,
                getattr(exc, "retry_after_seconds", None),
            )
            sleep(wait)
        except Exception:
            raise
    assert last_error is not None
    raise last_error
