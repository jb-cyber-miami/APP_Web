"""Timezone helpers. The database stores naive UTC; days are in the user's timezone."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones


def user_zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_valid_timezone(tz_name: str) -> bool:
    return tz_name in available_timezones()


def today_for(tz_name: str) -> date:
    return datetime.now(user_zone(tz_name)).date()


def to_local(dt_utc: datetime | None, tz_name: str) -> datetime | None:
    if dt_utc is None:
        return None
    return dt_utc.replace(tzinfo=UTC).astimezone(user_zone(tz_name))


def to_utc_naive(dt: datetime) -> datetime:
    """Normalise an aware datetime to naive UTC (naive input is assumed UTC)."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def day_bounds_utc(day: date, tz_name: str) -> tuple[datetime, datetime]:
    zone = user_zone(tz_name)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = start + timedelta(days=1)
    return to_utc_naive(start), to_utc_naive(end)
