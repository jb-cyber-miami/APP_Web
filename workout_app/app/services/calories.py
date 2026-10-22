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


def entry_trained(entry) -> bool:
    """A legacy entry (no EntryPlan) or a tracked entry with at least one logged set."""
    return entry.plan is None or bool(entry.set_logs)


def session_trained(session: WorkoutSession) -> bool:
    """Did this session record training? (the "went to the gym" rule, see ARCHITECTURE.md)

    Starting today's plan creates untouched tracked entries straight away; those alone are
    not training. A session counts when it has a legacy entry or a logged set, or when it was
    finished and is either empty (a quick "I went") or has wearable/manual calories.
    """
    if any(entry_trained(e) for e in session.entries):
        return True
    return session.ended_at is not None and (not session.entries or session.calories is not None)


@dataclass
class DaySummary:
    day: date
    sessions: list[WorkoutSession]
    session_kcal: int
    daily_active: dict[str, int] = field(default_factory=dict)  # per source
    daily_total: dict[str, int] = field(default_factory=dict)

    @property
    def went_to_gym(self) -> bool:
        return any(session_trained(s) for s in self.sessions)

    @property
    def headline_active(self) -> tuple[int | None, str | None]:
        for source in SOURCE_PRIORITY:
            if source in self.daily_active:
                return self.daily_active[source], source
        return None, None

    @property
    def logged_entries(self) -> list:
        """Entries that recorded training (legacy, or tracked with >= 1 logged set)."""
        return [e for s in self.sessions for e in s.entries if entry_trained(e)]

    @property
    def planned_entries(self) -> list:
        """Every entry of the day, logged or not (shown dimmed when nothing was logged)."""
        return [e for s in self.sessions for e in s.entries]

    @property
    def exercise_names(self) -> list[str]:
        """Distinct names of the exercises actually done (untouched planned ones are left out)."""
        names: list[str] = []
        for e in self.logged_entries:
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
