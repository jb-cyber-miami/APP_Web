"""Muscle recovery model (docs/AI_COACH.md section 3, formula verbatim).

Deterministic, no AI. ``compute_recovery`` is pure; ``load_work_sets`` reads the database.
All datetimes are naive UTC.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy.orm import selectinload

from ..models import SessionEntry, WorkoutSession, utcnow
from .muscles import CANONICAL, LABELS, LARGE, MuscleGroup, entry_muscles, library_maps

Rating = Literal["easy", "ok", "hard"]
Status = Literal["fresh", "recovering", "fatigued"]

LOOKBACK = timedelta(days=7)
NEGLECT_WINDOW = timedelta(days=14)
FRESH, RECOVERING = 85, 50
READY_SEARCH_HOURS = 96
RATING_MULT = {"easy": 0.8, "ok": 1.0, "hard": 1.25}
WEEKLY_TARGET: dict[str, int] = {m: (10 if m in LARGE else 6) for m in CANONICAL}
NEGLECT_EXCLUDE = frozenset({"forearms"})  # trained indirectly by pulling work
NEGLECT_DAYS = 6
STATUS_LABELS = {"fresh": "Fresh", "recovering": "Recovering", "fatigued": "Fatigued"}


@dataclass(frozen=True)
class WorkSet:
    session_id: int
    performed_at: datetime  # naive UTC
    primary: MuscleGroup | None
    secondary: tuple[MuscleGroup, ...]
    rating: Rating | None = None


@dataclass(frozen=True)
class MuscleRecovery:
    muscle: MuscleGroup
    recovery: int  # 0..100
    status: Status
    last_trained_at: datetime | None  # UTC, None = not trained in the lookback
    hours_since: float | None
    sets_7d: float  # effective sets in the lookback (1 decimal)
    ready_at: datetime | None  # UTC when recovery reaches 85, None if already fresh


RecoveryMap = dict[MuscleGroup, MuscleRecovery]


def _round(x: float) -> int:
    """Round half up (Python's round() is banker's rounding)."""
    return int(math.floor(x + 0.5))


def _status(value: int) -> Status:
    if value >= FRESH:
        return "fresh"
    if value >= RECOVERING:
        return "recovering"
    return "fatigued"


def _effective(ws: WorkSet, muscle: str) -> float:
    if ws.primary == muscle:
        role = 1.0
    elif muscle in ws.secondary:
        role = 0.5
    else:
        return 0.0
    return role * RATING_MULT.get(ws.rating or "ok", 1.0)


@dataclass(frozen=True)
class _Dose:
    dose: float
    last_at: datetime


def _doses(sets: list[WorkSet], muscle: str) -> list[_Dose]:
    by_session: dict[int, list[float | datetime]] = {}
    for ws in sets:
        e = _effective(ws, muscle)
        if e <= 0:
            continue
        cur = by_session.get(ws.session_id)
        if cur is None:
            by_session[ws.session_id] = [e, ws.performed_at]
        else:
            cur[0] += e
            cur[1] = max(cur[1], ws.performed_at)
    return [_Dose(float(d), last) for d, last in by_session.values()]


def _recovery_at(doses: list[_Dose], muscle: str, at: datetime) -> int:
    size = 1.0 if muscle in LARGE else 0.85
    remaining = 1.0
    for d in doses:
        t = max(0.0, (at - d.last_at).total_seconds() / 3600)
        r_hours = size * (24 + 48 * min(d.dose, 10) / 10)
        peak = min(1.0, d.dose / 8)
        residual = peak * (1 - t / r_hours) if t < r_hours else 0.0
        remaining *= 1 - residual
    return max(0, min(100, _round(100 * remaining)))


def compute_recovery(sets: Iterable[WorkSet], now: datetime) -> RecoveryMap:
    """Pure: recovery for all 11 canonical groups from the sets in the 7-day lookback."""
    since = now - LOOKBACK
    window = [s for s in sets if s.primary is not None and since <= s.performed_at <= now]
    out: RecoveryMap = {}
    for m in CANONICAL:
        doses = _doses(window, m)
        value = _recovery_at(doses, m, now) if doses else 100
        last = max((d.last_at for d in doses), default=None)
        total = sum(_effective(s, m) for s in window)
        ready_at = None
        if value < FRESH:
            for h in range(1, READY_SEARCH_HOURS + 1):
                at = now + timedelta(hours=h)
                if _recovery_at(doses, m, at) >= FRESH:
                    ready_at = at
                    break
        out[m] = MuscleRecovery(
            muscle=m,
            recovery=value,
            status=_status(value),
            last_trained_at=last,
            hours_since=round((now - last).total_seconds() / 3600, 1) if last else None,
            sets_7d=round(total, 1),
            ready_at=ready_at,
        )
    return out


# ---------------------------------------------------------------------------
# Database loader
# ---------------------------------------------------------------------------
def load_work_sets(user_id: int, since: datetime) -> list[WorkSet]:
    """WorkSets of the user's sessions started on/after ``since - 1 day``.

    Tracked entries (with an EntryPlan) give one WorkSet per logged set and nothing when no set
    is logged yet. Legacy entries give ``sets_done`` identical WorkSets at the session's end
    (or start) time. Cardio entries (no primary muscle) are skipped.
    """
    sessions = (
        WorkoutSession.query.filter(
            WorkoutSession.user_id == user_id, WorkoutSession.started_at >= since - timedelta(days=1)
        )
        .options(
            selectinload(WorkoutSession.entries).selectinload(SessionEntry.set_logs),
            selectinload(WorkoutSession.entries).selectinload(SessionEntry.plan),
        )
        .order_by(WorkoutSession.started_at)
        .all()
    )
    if not sessions:
        return []
    maps = library_maps(user_id)
    out: list[WorkSet] = []
    for ws in sessions:
        for entry in ws.entries:
            mm = entry_muscles(entry, maps)
            if mm.primary is None:
                continue
            if entry.set_logs:
                for log in entry.set_logs:
                    rating = log.rating if log.rating in RATING_MULT else None
                    out.append(WorkSet(ws.id, log.logged_at, mm.primary, mm.secondary, rating))
            elif entry.plan is None:
                at = ws.ended_at or ws.started_at
                for _ in range(max(0, min(50, entry.sets_done or 0))):
                    out.append(WorkSet(ws.id, at, mm.primary, mm.secondary, None))
    return [s for s in out if s.performed_at >= since]


def recovery_map(user_id: int, now: datetime | None = None) -> RecoveryMap:
    now = now or utcnow()
    return compute_recovery(load_work_sets(user_id, now - LOOKBACK), now)


def neglected_muscles(user_id: int, now: datetime | None = None) -> list[MuscleGroup]:
    """At most 3 muscles that are under-trained in the last 14 days (AI_COACH 3.5)."""
    now = now or utcnow()
    sets = [s for s in load_work_sets(user_id, now - NEGLECT_WINDOW) if s.performed_at <= now]
    return _neglected(sets, now)


def _neglected(sets: list[WorkSet], now: datetime) -> list[MuscleGroup]:
    if not sets:
        return []  # no recent history: don't nag new users
    last: dict[str, datetime] = {}
    volume: dict[str, float] = defaultdict(float)
    for s in sets:
        for m in CANONICAL:
            e = _effective(s, m)
            if e > 0:
                volume[m] += e
                if m not in last or s.performed_at > last[m]:
                    last[m] = s.performed_at
    candidates: list[tuple[float, int, MuscleGroup]] = []
    for idx, m in enumerate(CANONICAL):
        if m in NEGLECT_EXCLUDE:
            continue
        trained = last.get(m)
        if trained is not None and now - trained < timedelta(days=NEGLECT_DAYS):
            continue
        if volume[m] >= WEEKLY_TARGET[m]:
            continue
        days = math.inf if trained is None else (now - trained).total_seconds() / 86400
        candidates.append((-days, idx, m))
    candidates.sort()
    return [m for _, _, m in candidates[:3]]


# ---------------------------------------------------------------------------
# View model (body map, chips, hero focus)
# ---------------------------------------------------------------------------
def rec_class(m: MuscleRecovery) -> str:
    """Fixed vocabulary of colour classes; never contradicts the status word."""
    if m.last_trained_at is None:
        return "rec-na"
    if m.recovery >= 85:
        return "rec-4"
    if m.recovery >= 70:
        return "rec-3"
    if m.recovery >= 50:
        return "rec-2"
    if m.recovery >= 25:
        return "rec-1"
    return "rec-0"


@dataclass(frozen=True)
class RecoveryView:
    muscle: MuscleGroup
    label: str
    pct: int
    status: str  # "fresh" | "recovering" | "fatigued"
    status_label: str  # "Fresh" | "Recovering" | "Fatigued"
    rec: str  # rec-* class
    trained: bool
    ready_in_h: int | None


@dataclass(frozen=True)
class RecoveryPanel:
    items: list[RecoveryView]
    by_muscle: dict[str, RecoveryView]
    groups: dict[str, list[RecoveryView]]
    desc: str
    has_history: bool


def _view(m: MuscleRecovery, now: datetime | None) -> RecoveryView:
    ready_in_h = None
    if m.ready_at is not None:
        ref = now if now is not None else m.ready_at - timedelta(hours=1)
        ready_in_h = max(1, math.ceil((m.ready_at - ref).total_seconds() / 3600))
    return RecoveryView(
        muscle=m.muscle,
        label=LABELS[m.muscle],
        pct=max(0, min(100, int(m.recovery))),
        status=m.status,
        status_label=STATUS_LABELS[m.status],
        rec=rec_class(m),
        trained=m.last_trained_at is not None,
        ready_in_h=ready_in_h,
    )


def recovery_panel(rmap: RecoveryMap, now: datetime | None = None) -> RecoveryPanel:
    now = now or utcnow()
    items = [_view(rmap[m], now) for m in CANONICAL if m in rmap]
    order = {m: i for i, m in enumerate(CANONICAL)}
    groups: dict[str, list[RecoveryView]] = {"fresh": [], "recovering": [], "fatigued": []}
    for v in items:
        groups[v.status].append(v)
    for key in groups:
        groups[key].sort(key=lambda v: (v.pct, order[v.muscle]))
    desc = " ".join(f"{v.label} {v.pct}% recovered, {v.status}." for v in items)
    return RecoveryPanel(
        items=items,
        by_muscle={v.muscle: v for v in items},
        groups=groups,
        desc=desc,
        has_history=any(v.trained for v in items),
    )
