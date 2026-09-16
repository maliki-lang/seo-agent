from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, List, Optional

from ..models import UpsertStats


@dataclass
class CollectorResult:
    source: str
    rows: List[Any] = field(default_factory=list)
    stats: UpsertStats = field(default_factory=UpsertStats)
    cost_usd: Decimal = Decimal("0")
    raw_record_ids: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    capability: Optional[str] = None
    raw_payloads: List[Any] = field(default_factory=list)


class Collector(ABC):
    source: str

    @abstractmethod
    def collect(self, **kwargs: Any) -> CollectorResult:
        raise NotImplementedError
