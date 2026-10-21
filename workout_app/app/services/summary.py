"""Finished-workout summary: duration, sets, volume, ratings, PRs and next-time targets."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from ..models import Exercise, SessionEntry, WorkoutSession, utcnow
from .muscles import CANONICAL, MuscleGroup, entry_muscles, equipment_keys, library_maps
from .plan_schema import PlanRowView, load_targets, range_text
from .progression import (
    Exposure,
    PerformedSet,
    Prescription,
    epley,
    exercise_key,
    from_lb,
    load_all_history,
    next_targets,
    parse_reps,
    parse_seconds,
    parse_weight,
    to_lb,
)

HISTORY_DAYS = 3650


@dataclass(frozen=True)
class PRRow:
    name: str
    exercise_id: int | None
    e1rm: float  # display unit
    delta: float | None  # vs the best earlier e1RM, display unit
    best_set: str


@dataclass(frozen=True)
class WorkoutSummary:
    minutes: int | None
    sets_done: int
    volume: int
    unit: str
    ratings: dict[str, int]
    rated_total: int
    prs: list[PRRow]
    muscles_worked: tuple[MuscleGroup, ...]
    next_time: list[PlanRowView]


def _num(value: float) -> str:
    value = round(value, 1)
    return str(int(value)) if value == int(value) else f"{value:g}"


def _performed_sets(entry: SessionEntry) -> tuple[PerformedSet, ...]:
    """This entry's sets as progression PerformedSets (tracked logs or legacy expansion)."""
    if entry.set_logs:
        return tuple(
            PerformedSet(
                reps=log.reps,
                seconds=log.seconds,
                weight_lb=to_lb(log.weight, log.unit) if log.weight is not None else None,
                rating=log.rating,
                target_reps=log.target_reps,
                target_seconds=log.target_seconds,
            )
            for log in entry.set_logs
        )
    if entry.plan is not None:
        return ()
    pw = parse_weight(entry.weight or "")
    weight_lb = to_lb(pw.value, pw.unit) if pw.value is not None else None
    reps = parse_reps(entry.reps_done or "")
    seconds = parse_seconds(entry.reps_done or "") if reps is None else None
    one = PerformedSet(
        reps=reps, seconds=seconds, weight_lb=weight_lb, rating=None, target_reps=None, target_seconds=None
    )
    return tuple(one for _ in range(max(0, min(50, entry.sets_done or 0))))


def _best_e1rm(sets) -> tuple[float | None, PerformedSet | None]:
    best, best_set = None, None
    for s in sets:
        if s.weight_lb is None or s.weight_lb <= 0 or s.reps is None:
            continue
        v = epley(s.weight_lb, s.reps)
        if v is not None and (best is None or v > best):
            best, best_set = v, s
    return best, best_set


def _working_weight_lb(sets) -> float | None:
    weights = [round(s.weight_lb, 2) for s in sets if s.weight_lb is not None]
    if not weights:
        return None
    counts = Counter(weights)
    return max(counts, key=lambda w: (counts[w], w))


def workout_summary(ws: WorkoutSession, *, unit: str, now: datetime | None = None) -> WorkoutSummary:
    now = now or utcnow()
    unit = unit if unit in ("lb", "kg") else "lb"
    maps = library_maps(ws.user_id)
    history, _, _ = load_all_history(ws.user_id, now=now, days=HISTORY_DAYS)
    performed_at = ws.ended_at or now

    sets_done = 0
    volume_lb = 0.0
    ratings = {"easy": 0, "ok": 0, "hard": 0}
    worked: set[str] = set()
    prs: list[PRRow] = []
    next_rows: list[PlanRowView] = []

    for entry in ws.entries:
        mm = entry_muscles(entry, maps)
        performed = _performed_sets(entry)
        if entry.set_logs:
            sets_done += len(entry.set_logs)
            for log in entry.set_logs:
                if log.rating in ratings:
                    ratings[log.rating] += 1
        elif entry.plan is None:
            sets_done += entry.sets_done or 0
        if performed:
            worked.update(mm.all)
        for s in performed:
            if s.weight_lb is not None and s.reps:
                volume_lb += s.weight_lb * s.reps

        key = exercise_key(entry.exercise_id, entry.exercise_name)
        earlier = [
            e for e in history.get(key, []) if e.session_id != ws.id and e.performed_at < ws.started_at
        ]
        best, best_set = _best_e1rm(performed)
        if best is not None:
            earlier_best = max(
                (v for v in (_best_e1rm(e.sets)[0] for e in earlier) if v is not None), default=None
            )
            if earlier_best is not None and best > earlier_best:
                prs.append(
                    PRRow(
                        name=entry.exercise_name,
                        exercise_id=entry.exercise_id,
                        e1rm=round(from_lb(best, unit), 1),
                        delta=round(from_lb(best - earlier_best, unit), 1),
                        best_set=f"{_num(from_lb(best_set.weight_lb, unit))} {unit} × {best_set.reps}",
                    )
                )

        if entry.plan is not None and performed:
            row = _next_time(entry, performed, earlier, mm, unit, performed_at, now, ws.id)
            if row is not None:
                next_rows.append(row)

    minutes = None
    if ws.ended_at is not None:
        minutes = max(0, int((ws.ended_at - ws.started_at).total_seconds() // 60))
    return WorkoutSummary(
        minutes=minutes,
        sets_done=sets_done,
        volume=int(round(from_lb(volume_lb, unit))) if volume_lb else 0,
        unit=unit,
        ratings=ratings,
        rated_total=sum(ratings.values()),
        prs=prs,
        muscles_worked=tuple(m for m in CANONICAL if m in worked),
        next_time=next_rows,
    )


def _next_time(entry, performed, earlier, mm, unit, performed_at, now, session_id) -> PlanRowView | None:
    plan = entry.plan
    targets = load_targets(plan.targets_json)
    if not targets:
        return None
    seconds = next((t.target_seconds for t in targets if t.target_seconds is not None), None)
    if plan.rep_low is not None and plan.rep_high is not None:
        rep_range = (plan.rep_low, plan.rep_high)
    elif seconds is None:
        reps = [t.target_reps for t in targets if t.target_reps is not None] or [10]
        rep_range = (min(reps), max(reps))
    else:
        rep_range = None
    exercise = entry_exercise(entry)
    rx = Prescription(
        sets=max(1, min(10, len(targets))),
        rep_range=rep_range,
        seconds=seconds,
        pattern=plan.pattern or mm.pattern,
        equipment=equipment_keys(f"{exercise.equipment if exercise else ''} {entry.exercise_name}"),
    )
    this = Exposure(session_id=session_id, performed_at=performed_at, sets=performed)
    history = [this] + sorted(earlier, key=lambda e: e.performed_at, reverse=True)
    result = next_targets(history, rx, unit=unit, now=now)
    tgts = list(result.targets)
    if not tgts:
        return None
    reps = [t.reps for t in tgts if t.reps is not None]
    secs = [t.seconds for t in tgts if t.seconds is not None]
    reps_text = (
        range_text(min(reps), max(reps)) if reps else (range_text(min(secs), max(secs), "s") if secs else "")
    )
    weights = [t.weight for t in tgts if t.weight is not None]
    weight = max(weights) if weights else None
    working = _working_weight_lb(performed)
    delta = None
    if weight is not None and working is not None:
        delta = round(weight - from_lb(working, unit), 1)
    return PlanRowView(
        name=entry.exercise_name,
        sets=len(tgts),
        reps_text=reps_text,
        weight=weight,
        unit=unit,
        weight_label="",
        note=result.reason,
        warning=False,
        primary=mm.primary or "",
        delta=delta,
    )


def entry_exercise(entry: SessionEntry) -> Exercise | None:
    if entry.exercise_id is None:
        return None
    from ..extensions import db

    ex = db.session.get(Exercise, entry.exercise_id)
    if ex is None or ex.user_id != entry.session.user_id:
        return None
    return ex
