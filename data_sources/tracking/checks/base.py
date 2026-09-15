from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict

from ..enums import CheckStatus, Severity


@dataclass
class CheckResult:
    check_name: str
    scope: str
    status: CheckStatus
    severity: Severity
    threshold: str
    observed_value: str
    details: Dict[str, Any] = field(default_factory=dict)


class Check(ABC):
    name: str
    severity: Severity = Severity.ERROR

    @abstractmethod
    def run(self, **kwargs: Any) -> CheckResult:
        raise NotImplementedError
