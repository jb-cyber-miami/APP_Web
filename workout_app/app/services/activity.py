"""Week strip, month calendar and weekly streak.

"Gym day" uses the same rule as ``calories.session_trained`` (``DaySummary.went_to_gym``):
a session with a legacy entry or a logged set, or a finished session that is empty or has
calories. A just-started plan (untouched tracked entries) is not a gym day.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import exists, or_, select

from ..extensions import db
from ..models import EntryPlan, SessionEntry, SetLog, WorkoutSession

DOW = ("M", "T", "W", "T", "F", "S", "S")
STREAK_LOOKBACK_DAYS = 3 * 365


@dataclass(frozen=True)
class DayCell:
    day: date
    dow: str
    in_month: bool
    is_gym: bool
    is_today: bool
    is_future: bool
    kcal: int | None
    aria: str


@dataclass(frozen=True)
class MonthView:
    year: int
    month: int
    title: str
    weeks: list[list[DayCell]]
    gym_days: int
    prev: str
    next: str | None


def _day_stats(user_id: int, start: date, end: date) -> dict[date, tuple[bool, int | None]]:
    """day -> (is_gym, Σ session kcal or None) for start <= day <= end, in one query."""
    has_entries = exists().where(SessionEntry.session_id == WorkoutSession.id)
    trained = exists().where(
        SessionEntry.session_id == WorkoutSession.id,
        or_(
            ~exists().where(EntryPlan.entry_id == SessionEntry.id),  # legacy entry
            exists().where(SetLog.entry_id == SessionEntry.id),  # tracked with a logged set
        ),
    )
    rows = db.session.execute(
        select(
            WorkoutSession.day, WorkoutSession.ended_at, WorkoutSession.calories, has_entries, trained
        ).where(WorkoutSession.user_id == user_id, WorkoutSession.day >= start, WorkoutSession.day <= end)
    ).all()
    out: dict[date, tuple[bool, int | None]] = {}
    for day, ended_at, kcal, entries, did_train in rows:
        gym, total = out.get(day, (False, None))
        finished = ended_at is not None and (not entries or kcal is not None)
        gym = gym or bool(did_train) or finished
        if kcal is not None:
            total = (total or 0) + int(kcal)
        out[day] = (gym, total)
    return out


def _aria(d: date, gym: bool, kcal: int | None, future: bool) -> str:
    label = f"{d:%b} {d.day}"
    if future:
        return label
    if gym:
        return f"{label}, gym day, {kcal} kcal" if kcal is not None else f"{label}, gym day"
    return f"{label}, no gym"


def _cell(d: date, today: date, stats, in_month: bool = True) -> DayCell:
    gym, kcal = stats.get(d, (False, None))
    future = d > today
    return DayCell(
        day=d,
        dow=DOW[d.weekday()],
        in_month=in_month,
        is_gym=gym and not future,
        is_today=d == today,
        is_future=future,
        kcal=kcal if not future else None,
        aria=_aria(d, gym, kcal, future),
    )


def week_strip(user, today: date) -> list[DayCell]:
    monday = today - timedelta(days=today.weekday())
    stats = _day_stats(user.id, monday, monday + timedelta(days=6))
    return [_cell(monday + timedelta(days=i), today, stats) for i in range(7)]


def month_calendar(user, year: int, month: int, today: date) -> MonthView:
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    grid_start = first - timedelta(days=first.weekday())
    grid_end = last + timedelta(days=6 - last.weekday())
    stats = _day_stats(user.id, grid_start, grid_end)
    weeks: list[list[DayCell]] = []
    d = grid_start
    while d <= grid_end:
        weeks.append(
            [
                _cell(d + timedelta(days=i), today, stats, (d + timedelta(days=i)).month == month)
                for i in range(7)
            ]
        )
        d += timedelta(days=7)
    gym_days = sum(1 for w in weeks for c in w if c.in_month and c.is_gym)
    prev_month = first - timedelta(days=1)
    next_first = last + timedelta(days=1)
    return MonthView(
        year=year,
        month=month,
        title=f"{first:%B} {year}",
        weeks=weeks,
        gym_days=gym_days,
        prev=f"{prev_month.year:04d}-{prev_month.month:02d}",
        next=f"{next_first.year:04d}-{next_first.month:02d}" if next_first <= today else None,
    )


def streak_weeks(user, today: date) -> int:
    """Consecutive ISO weeks with >= 1 gym day, ending this week (or last week if none yet)."""
    stats = _day_stats(user.id, today - timedelta(days=STREAK_LOOKBACK_DAYS), today)
    weeks = {d - timedelta(days=d.weekday()) for d, (gym, _) in stats.items() if gym}
    week = today - timedelta(days=today.weekday())
    if week not in weeks:
        week -= timedelta(days=7)
    count = 0
    while week in weeks:
        count += 1
        week -= timedelta(days=7)
    return count
