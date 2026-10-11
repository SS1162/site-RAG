"""Custom column types."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetime stored as a fixed-width ISO-8601 string.

    Fixed width (always microseconds and ``+00:00``) keeps text ordering equal to time
    ordering, so comparisons and ORDER BY work in SQL. Naive datetimes are rejected.
    """

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        """Convert an aware datetime to its UTC ISO string."""
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"Naive datetime not allowed, use timezone-aware UTC: {value!r}")
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        """Parse the stored ISO string back to an aware UTC datetime."""
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
