from __future__ import annotations


class TrackingError(Exception):
    """Base class for tracking failures."""

    retryable = False
    error_code = "TrackingError"

    def __init__(self, message: str, *, redacted_detail: str = ""):
        super().__init__(message)
        self.redacted_detail = redacted_detail or message


class ConfigurationError(TrackingError):
    error_code = "ConfigurationError"


class AuthenticationError(TrackingError):
    error_code = "AuthenticationError"


class PermissionDenied(TrackingError):
    error_code = "PermissionDenied"


class RateLimitError(TrackingError):
    retryable = True
    error_code = "RateLimitError"

    def __init__(self, message: str, *, retry_after_seconds: float | None = None, redacted_detail: str = ""):
        super().__init__(message, redacted_detail=redacted_detail)
        self.retry_after_seconds = retry_after_seconds


class TransientNetworkError(TrackingError):
    retryable = True
    error_code = "TransientNetworkError"


class ProviderServerError(TrackingError):
    retryable = True
    error_code = "ProviderServerError"


class InvalidResponseError(TrackingError):
    error_code = "InvalidResponseError"


class SchemaMismatchError(TrackingError):
    error_code = "SchemaMismatchError"


class CostLimitExceeded(TrackingError):
    error_code = "CostLimitExceeded"


class DataQualityError(TrackingError):
    error_code = "DataQualityError"


class RunLockError(TrackingError):
    error_code = "RunLockError"


class UnsupportedCapability(TrackingError):
    error_code = "UnsupportedCapability"
