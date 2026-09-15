from __future__ import annotations

from datetime import date, timedelta
from typing import Iterator, Tuple


def date_chunks(start: date, end: date, size: int = 7) -> Iterator[Tuple[date, date]]:
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=size - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)
