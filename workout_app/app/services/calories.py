"""Calorie math: per workout session and per day.

Oura and Apple Watch both measure the same body, so their numbers are never
added together. Apple Watch is preferred when present (wrist sensors track gym
work better than a ring); Oura is the fallback; a manual entry always wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..models import HealthSample, WorkoutSession

SOURCE_PRIORITY = ("apple", "oura")
SLACK = timedelta(minutes=10)  # wearables often start/stop a bit off from the app


def _overlap_fraction(s_start: datetime, s_end: datetime, w_start: datetime, w_end: datetime) -> float:
    total = (s_end - s_start).total_seconds()
    if total <= 0:
        return 1.0 if w_start - SLACK <= s_start <= w_end + SLACK else 0.0
    overlap = (min(s_end, w_end + SLACK) - max(s_start, w_start - SLACK)).total_seconds()
    return max(0.0, min(1.0, overlap / total))


def session_calories(session: WorkoutSession) -> tuple[int | None, str | None]:
    """Active calories recorded by a wearable during the session's time window."""
    if session.ended_at is None:
        return None, None
    window_start = session.started_at - SLACK
    window_end = session.ended_at + SLACK
    samples = HealthSample.query.filter(
        HealthSample.user_id == session.user_id,
        HealthSample.kind == "workout",
        HealthSample.start_at < window_end,
        HealthSample.end_at > window_start,
    ).all()

    totals: dict[str, float] = {}
    for s in samples:
        fraction = _overlap_fraction(s.start_at, s.end_at, session.started_at, session.ended_at)
        if fraction > 0:
            totals[s.source] = totals.get(s.source, 0) + s.active_kcal * fraction
    for source in SOURCE_PRIORITY:
        if source in totals:
            return round(totals[source]), source
    return None, None


@dataclass
class DaySummary:
    day: date
    sessions: list[WorkoutSession]
    session_kcal: int
    daily_active: dict[str, int] = field(default_factory=dict)  # per source
    daily_total: dict[str, int] = field(default_factory=dict)

    @property
    def went_to_gym(self) -> bool:
        return any(s.ended_at is not None or s.entries for s in self.sessions)

    @property
    def headline_active(self) -> tuple[int | None, str | None]:
        for source in SOURCE_PRIORITY:
            if source in self.daily_active:
                return self.daily_active[source], source
        return None, None

    @property
    def exercise_names(self) -> list[str]:
        names: list[str] = []
        for s in self.sessions:
            for e in s.entries:
                if e.exercise_name not in names:
                    names.append(e.exercise_name)
        return names


def day_summary(user, day: date) -> DaySummary:
    sessions = (
        WorkoutSession.query.filter_by(user_id=user.id, day=day).order_by(WorkoutSession.started_at).all()
    )
    summary = DaySummary(day=day, sessions=sessions, session_kcal=sum(s.calories or 0 for s in sessions))
    for s in HealthSample.query.filter_by(user_id=user.id, kind="daily", day=day).all():
        summary.daily_active[s.source] = s.active_kcal
        if s.total_kcal:
            summary.daily_total[s.source] = s.total_kcal
    return summary


def recent_days(user, today: date, count: int = 14) -> list[DaySummary]:
    return [day_summary(user, today - timedelta(days=i)) for i in range(count)]
