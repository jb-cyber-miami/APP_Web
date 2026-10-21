"""Set-by-set logging and the per-entry view model of a workout session.

A ``SetLog`` row exists only for a COMPLETED set. Rows that are not done yet are rendered
from the entry's ``EntryPlan`` targets. Targets on a ``SetLog`` are always copied server-side
from the ``EntryPlan``, never taken from the form. Nothing here commits; routes do.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..extensions import db
from ..models import Exercise, SessionEntry, SetLog, WorkoutSession, utcnow
from .muscles import RATINGS, MuscleMap, equipment_keys, muscles_for
from .plan_schema import PlannedSet, dump_targets, load_targets
from .progression import fmt_weight

MAX_SETS = 20
WEIGHTED = frozenset(
    {"dumbbells", "barbell", "cable_machine", "smith_machine", "leg_press", "machines", "kettlebells"}
)


@dataclass(frozen=True)
class SetRowView:
    set_index: int
    number: int  # set_index + 1
    target_reps: int | None
    target_seconds: int | None
    target_weight: float | None
    target_text: str  # "135 × 10", "BW × 12", "30s", "— × 8"
    log: SetLog | None  # None = not done yet
    weight_value: str  # prefill: log.weight or target weight, "" if None
    reps_value: str
    seconds_value: str
    is_done: bool
    is_current: bool  # first not-done row of the current entry
    timed: bool


@dataclass(frozen=True)
class EntryView:
    entry: SessionEntry
    exercise: Exercise | None
    tracked: bool
    rows: list[SetRowView]
    done: int
    planned: int
    rest_seconds: int
    note: str
    weight_label: str
    muscles: MuscleMap
    is_current: bool
    is_complete: bool
    can_swap: bool


def num_text(value: float | None) -> str:
    """135.0 -> "135", 62.5 -> "62.5", None -> ""."""
    if value is None:
        return ""
    value = round(float(value), 2)
    return str(int(value)) if value == int(value) else f"{value:g}"


def _is_bodyweight(exercise: Exercise | None, name: str) -> bool:
    text = f"{exercise.equipment if exercise else ''} {name}"
    return not (equipment_keys(text) & WEIGHTED)


def _target_text(t: PlannedSet | None, bodyweight: bool) -> str:
    if t is None:
        return ""
    if t.target_seconds is not None:
        secs = f"{t.target_seconds}s"
        return f"{num_text(t.weight)} × {secs}" if t.weight is not None else secs
    if t.weight is not None:
        w = num_text(t.weight)
    else:
        w = "BW" if bodyweight else "—"
    return f"{w} × {t.target_reps}"


def entry_targets(entry: SessionEntry) -> list[PlannedSet]:
    return load_targets(entry.plan.targets_json) if entry.plan is not None else []


def planned_count(entry: SessionEntry, targets: list[PlannedSet] | None = None) -> int:
    targets = entry_targets(entry) if targets is None else targets
    top = max((log.set_index + 1 for log in entry.set_logs), default=0)
    return max(len(targets), top)


def _rows(entry, targets, logs, bodyweight, current: bool) -> list[SetRowView]:
    count = max(len(targets), max(logs, default=-1) + 1)
    first_open = next((i for i in range(count) if i not in logs), None)
    rows = []
    for i in range(count):
        t = targets[i] if i < len(targets) else (targets[-1] if targets else None)
        log = logs.get(i)
        timed = (t is not None and t.target_seconds is not None) or (
            log is not None and log.seconds is not None
        )
        if log is not None:
            weight_value = num_text(log.weight)
            reps_value = "" if log.reps is None else str(log.reps)
            seconds_value = "" if log.seconds is None else str(log.seconds)
        else:
            weight_value = num_text(t.weight) if t else ""
            reps_value = str(t.target_reps) if t and t.target_reps is not None else ""
            seconds_value = str(t.target_seconds) if t and t.target_seconds is not None else ""
        rows.append(
            SetRowView(
                set_index=i,
                number=i + 1,
                target_reps=t.target_reps if t else None,
                target_seconds=t.target_seconds if t else None,
                target_weight=t.weight if t else None,
                target_text=_target_text(t, bodyweight),
                log=log,
                weight_value=weight_value,
                reps_value=reps_value,
                seconds_value=seconds_value,
                is_done=log is not None,
                is_current=current and i == first_open,
                timed=timed,
            )
        )
    return rows


def entry_views(ws: WorkoutSession, *, library: dict[int, Exercise]) -> list[EntryView]:
    views: list[EntryView] = []
    current_found = False
    for entry in ws.entries:
        exercise = library.get(entry.exercise_id) if entry.exercise_id is not None else None
        if exercise is not None and exercise.user_id != ws.user_id:
            exercise = None
        tracked = entry.plan is not None
        if tracked:
            targets = entry_targets(entry)
            logs = {log.set_index: log for log in entry.set_logs}
            planned = planned_count(entry, targets)
            done = len(logs)
            current = ws.ended_at is None and not current_found and done < planned
            current_found = current_found or current
            rows = _rows(entry, targets, logs, _is_bodyweight(exercise, entry.exercise_name), current)
            plan = entry.plan
            views.append(
                EntryView(
                    entry=entry,
                    exercise=exercise,
                    tracked=True,
                    rows=rows,
                    done=done,
                    planned=planned,
                    rest_seconds=max(0, min(600, plan.rest_seconds or 0)),
                    note=plan.progression_note or "",
                    weight_label="suggested" if any(t.weight_source == "ai" for t in targets) else "",
                    muscles=muscles_for(exercise, entry.exercise_name),
                    is_current=current,
                    is_complete=done >= planned,
                    can_swap=done == 0 and ws.ended_at is None,
                )
            )
        else:
            done = entry.sets_done or 0
            views.append(
                EntryView(
                    entry=entry,
                    exercise=exercise,
                    tracked=False,
                    rows=[],
                    done=done,
                    planned=done,
                    rest_seconds=exercise.rest_seconds if exercise else 90,
                    note="",
                    weight_label="",
                    muscles=muscles_for(exercise, entry.exercise_name),
                    is_current=False,
                    is_complete=True,
                    can_swap=False,
                )
            )
    return views


# ---------------------------------------------------------------------------
# Mutations (no commit)
# ---------------------------------------------------------------------------
def log_set(
    entry: SessionEntry,
    *,
    set_index: int,
    reps: int | None,
    seconds: int | None,
    weight: float | None,
    rating: str | None,
    unit: str,
) -> SetLog:
    """Upsert the completed set ``set_index``; targets are copied from the EntryPlan."""
    if not 0 <= set_index < MAX_SETS:
        raise ValueError("set_index out of range")
    if rating not in RATINGS:
        rating = None
    targets = entry_targets(entry)
    target = targets[set_index] if set_index < len(targets) else None
    log = next((s for s in entry.set_logs if s.set_index == set_index), None)
    if log is None:
        log = SetLog(user_id=entry.session.user_id, set_index=set_index)
        entry.set_logs.append(log)
        db.session.add(log)
    log.reps = reps
    log.seconds = seconds
    log.weight = None if weight is None else max(0.0, min(2000.0, float(weight)))
    log.unit = unit if unit in ("lb", "kg") else "lb"
    log.rating = rating
    log.target_reps = target.target_reps if target else None
    log.target_seconds = target.target_seconds if target else None
    log.target_weight = target.weight if target else None
    log.logged_at = utcnow()
    entry.set_logs.sort(key=lambda s: s.set_index)
    sync_entry(entry)
    return log


def delete_set(log: SetLog) -> None:
    entry = log.entry
    if log in entry.set_logs:
        entry.set_logs.remove(log)  # delete-orphan cascade removes the row
    db.session.delete(log)
    sync_entry(entry)


def rate_set(log: SetLog, rating: str | None) -> None:
    log.rating = rating if rating in RATINGS else None


def add_set(entry: SessionEntry) -> None:
    """Append a copy of the last target (max 20 sets). No-op for legacy entries."""
    plan = entry.plan
    if plan is None:
        return
    targets = entry_targets(entry)
    if not targets or len(targets) >= MAX_SETS:
        return
    targets.append(targets[-1].model_copy())
    plan.targets_json = dump_targets(targets)
    sync_entry(entry)


def sync_entry(entry: SessionEntry) -> None:
    """Keep the legacy summary fields of a tracked entry in step with its set logs."""
    plan = entry.plan
    if plan is None:
        return
    logs = sorted(entry.set_logs, key=lambda s: s.set_index)
    entry.sets_done = len(logs)
    if logs:
        last = logs[-1]
        if last.reps is not None:
            reps_text = str(last.reps)
        elif last.seconds is not None:
            reps_text = f"{last.seconds}s"
        else:
            reps_text = ""
        weight_text = fmt_weight(last.weight, last.unit) if last.weight is not None else ""
    else:
        targets = entry_targets(entry)
        first = targets[0] if targets else None
        if first is None:
            reps_text = ""
        elif first.target_reps is not None:
            reps_text = str(first.target_reps)
        else:
            reps_text = f"{first.target_seconds}s"
        weight_text = ""
    entry.reps_done = reps_text[:20]
    entry.weight = (weight_text or "")[:20]


def unlogged_sets(ws: WorkoutSession) -> int:
    total = 0
    for entry in ws.entries:
        if entry.plan is not None:
            total += max(0, planned_count(entry) - len(entry.set_logs))
    return total


def progress(ws: WorkoutSession) -> tuple[int, int]:
    """(exercises complete, tracked exercises)."""
    complete = tracked = 0
    for entry in ws.entries:
        if entry.plan is None:
            continue
        tracked += 1
        if len(entry.set_logs) >= planned_count(entry):
            complete += 1
    return complete, tracked
