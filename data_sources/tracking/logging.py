from __future__ import annotations

import json
import logging
from typing import Any, Optional

SECRET_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "x-api-key",
    "cookie",
    "private_key",
    "client_secret",
    "app_secret",
    "password",
    "token",
}


def redact_value(key: str, value: Any) -> Any:
    lowered = key.lower().replace("_", "-")
    if any(part in lowered for part in SECRET_KEYS):
        return "[redacted]"
    return value


def redact_mapping(data: Optional[dict]) -> dict:
    if not data:
        return {}
    return {k: redact_value(str(k), v) for k, v in data.items()}


class StructuredLogger:
    def __init__(self, name: str = "tracking", level: str = "INFO"):
        self._logger = logging.getLogger(name)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    def log(self, event: str, **fields: Any) -> None:
        payload = {"event": event, **redact_mapping(fields)}
        self._logger.info(json.dumps(payload, default=str, sort_keys=True))

    def warning(self, event: str, **fields: Any) -> None:
        payload = {"event": event, **redact_mapping(fields)}
        self._logger.warning(json.dumps(payload, default=str, sort_keys=True))

    def error(self, event: str, **fields: Any) -> None:
        payload = {"event": event, **redact_mapping(fields)}
        self._logger.error(json.dumps(payload, default=str, sort_keys=True))
