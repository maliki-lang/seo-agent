from __future__ import annotations

import threading
from decimal import Decimal

from .exceptions import CostLimitExceeded


class CostLedger:
    def __init__(self, cap_usd: Decimal):
        self.cap_usd = cap_usd
        self.spent = Decimal("0")
        self._lock = threading.Lock()

    def add(self, amount: Decimal, *, source: str) -> None:
        amount = Decimal(amount)
        if amount < 0:
            raise CostLimitExceeded(f"Negative cost is invalid for {source}")
        with self._lock:
            if self.spent + amount > self.cap_usd:
                raise CostLimitExceeded(
                    f"Cost cap {self.cap_usd} USD exceeded by {source} "
                    f"(spent {self.spent}, adding {amount})"
                )
            self.spent += amount
