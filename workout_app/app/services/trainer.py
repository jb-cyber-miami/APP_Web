"""AI coach orchestration (docs/AI_COACH.md sections 6-8, docs/BUILD_CONTRACT.md 2.12).

Builds the ``TrainerContext``, asks Claude (POST paths only) or the deterministic fallback for
today's plan, merges progression targets into it ("history wins"), persists ``DailyPlan`` and
turns a plan into a tracked ``WorkoutSession``. Nothing here commits; routes do.

``exercise_id`` values read from stored JSON are re-checked against the user's library
(``Exercise.user_id == user.id``) and treated as ``None`` otherwise.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from flask import current_app

from ..extensions import db
from ..models import DailyPlan, EntryPlan, Exercise, GymProfile, SessionEntry, WorkoutSession, utcnow
from ..timeutil import to_local
from . import ai_coach
from .catalog import CATALOG, CatalogExercise, find
from .muscles import (
    CANONICAL,
    EQUIPMENT_KEYS,
    FOCUS_MUSCLES,
    LABELS,
    SWAP_REASONS,
    MuscleGroup,
    MuscleMap,
    available,
    ensure_muscles,
    equipment_keys,
    muscles_for,
    normalize_name,
)
from .plan_schema import (
    LibraryItem,
    PlannedExercise,
    PlannedSet,
    RecentExercise,
    RecentSession,
    TodayPlan,
    TrainerContext,
    dump_exercises,
    dump_targets,
    estimate_minutes,
    load_exercises,
    load_plan,
    load_targets,
)
from .prefs import default_minutes_for, unit_for
from .progression import (
    Prescription,
    exercise_key,
    fmt_weight,
    from_lb,
    increment,
    load_history,
    next_targets,
    parse_rep_range,
    parse_reps,
    parse_seconds,
    parse_weight,
    round_load,
    to_lb,
    to_planned_sets,
)
from .recovery import neglected_muscles, recovery_map

log = logging.getLogger(__name__)

COMPOUND_PATTERNS = frozenset(
    {"horizontal_push", "vertical_push", "horizontal_pull", "vertical_pull", "squat", "hinge", "lunge"}
)
NOTICE_NO_AI = "Built from your history and recovery."
NOTICE_AI_DOWN = "The AI coach is unavailable right now, so this plan was built from your history."
NOTICE_BUDGET = "You've used today's AI coach requests. Here's a plan from your history."
NOTICE_MAX_GENERATIONS = "You've reached today's AI rebuilds. Here's a plan from your history."
NOTICE_SWAP_AI_DOWN = "The AI coach is unavailable right now, so these options come from your library."
NOTICE_SWAP_BUDGET = "You've used today's AI coach requests, so these options come from your library."
RECENT_SESSIONS = 5
RECENT_LINES = 40
LIBRARY_LIMIT = 60

# Commercial-gym defaults when a user never filled in their gym profile (AI_COACH 8, see doc).
_ASSUMED_EQUIPMENT = {
    "commercial": frozenset(k for k in EQUIPMENT_KEYS if k != "trx"),
    "small": frozenset({"bodyweight", "dumbbells", "bench", "cable_machine", "machines", "pull_up_bar"}),
    "home": frozenset({"bodyweight"}),
}


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------
def _now(now: datetime | None) -> datetime:
    return now or utcnow()


def _local_day(user, now: datetime):
    return to_local(now, user.timezone).date()


def _gym_equipment(gym: GymProfile | None) -> frozenset:
    if gym is None or not gym.equipment:
        size = gym.gym_size if gym is not None else "commercial"
        return _ASSUMED_EQUIPMENT.get(size, frozenset({"bodyweight"}))
    return available(gym.equipment)


def _exercise_equipment(ex: Exercise) -> frozenset:
    """Equipment a library exercise needs: its text, its name and its catalog twin."""
    keys = set(equipment_keys(ex.equipment or "")) | set(equipment_keys(ex.name or ""))
    twin = find(ex.name)
    if twin is not None:
        keys |= twin.requires
    keys.discard("bodyweight")
    return frozenset(keys) or frozenset({"bodyweight"})


def _is_timed(reps_text: str) -> bool:
    return parse_reps(reps_text or "") is None and parse_seconds(reps_text or "") is not None


def _rating_word(logs) -> str:
    ratings = [s.rating for s in logs if s.rating]
    if "hard" in ratings:
        return " (hard)"
    if ratings and all(r == "easy" for r in ratings):
        return " (easy)"
    return ""


def _entry_summary(entry: SessionEntry) -> str:
    logs = list(entry.set_logs or [])
    if logs:
        reps = [s.reps for s in logs if s.reps is not None]
        secs = [s.seconds for s in logs if s.seconds is not None]
        if reps:
            body = (
                f"{len(logs)}×{reps[0]}"
                if len(set(reps)) == 1
                else "×".join([str(len(logs)), "/".join(map(str, reps))])
            )
        else:
            body = f"{len(logs)}×{secs[0] if secs else '?'}s"
        weights = [s for s in logs if s.weight is not None]
        weight = f" @ {fmt_weight(weights[-1].weight, weights[-1].unit)}" if weights else " bodyweight"
        return f"{body}{weight}{_rating_word(logs)}"
    if entry.plan is not None:
        return ""
    weight = f" @ {entry.weight}" if (entry.weight or "").strip() else " bodyweight"
    return f"{entry.sets_done}×{entry.reps_done or '?'}{weight}"


def build_context(
    user, *, now: datetime | None = None, minutes: int | None = None, focus: str = "auto", extra: str = ""
) -> TrainerContext:
    now = _now(now)
    gym = GymProfile.query.filter_by(user_id=user.id).first()
    library_rows = Exercise.query.filter_by(user_id=user.id).all()
    owned = {e.id: e for e in library_rows}

    sessions = (
        WorkoutSession.query.filter(
            WorkoutSession.user_id == user.id, WorkoutSession.started_at >= now - timedelta(days=180)
        )
        .order_by(WorkoutSession.started_at.desc())
        .all()
    )
    last_used: dict[int, datetime] = {}
    recent: list[RecentSession] = []
    lines = 0
    for ws in sessions:
        if ws.started_at > now:
            continue
        when = ws.ended_at or ws.started_at
        for entry in ws.entries:
            has_sets = bool(entry.set_logs) or (entry.plan is None and (entry.sets_done or 0) > 0)
            if has_sets and entry.exercise_id in owned and entry.exercise_id not in last_used:
                last_used[entry.exercise_id] = when
        if ws.ended_at is None or len(recent) >= RECENT_SESSIONS or lines >= RECENT_LINES:
            continue
        items = []
        for entry in ws.entries:
            summary = _entry_summary(entry)
            if not summary or lines >= RECENT_LINES:
                continue
            mm = muscles_for(owned.get(entry.exercise_id), entry.exercise_name)
            items.append(RecentExercise(entry.exercise_name, mm.primary, summary))
            lines += 1
        if items:
            recent.append(RecentSession(ws.day, tuple(items)))

    library: list[LibraryItem] = []
    for ex in library_rows:
        mm = muscles_for(ex)
        library.append(
            LibraryItem(
                exercise_id=ex.id,
                name=ex.name,
                primary=mm.primary,
                equipment=_exercise_equipment(ex),
                secondary=mm.secondary,
                pattern=mm.pattern,
                timed=_is_timed(ex.reps),
                last_used=last_used.get(ex.id),
            )
        )
    library.sort(
        key=lambda i: (i.last_used is None, -(i.last_used.timestamp() if i.last_used else 0), i.name.lower())
    )

    return TrainerContext(
        today=_local_day(user, now),
        now=now,
        gym_size=(gym.gym_size if gym else "commercial"),
        equipment=_gym_equipment(gym),
        goal=(gym.goal if gym else "general fitness"),
        experience=(gym.experience if gym else "intermediate"),
        notes=(gym.notes if gym else "") or "",
        unit=unit_for(user.id),
        minutes=max(10, min(120, int(minutes or default_minutes_for(user.id)))),
        focus=focus if focus in FOCUS_MUSCLES else "auto",
        extra=(extra or "")[:300],
        recovery=recovery_map(user.id, now),
        neglected=neglected_muscles(user.id, now),
        recent=recent,
        library=library[:LIBRARY_LIMIT],
        user_id=user.id,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _rec(ctx: TrainerContext, muscle: str) -> int:
    rec = (ctx.recovery or {}).get(muscle)
    return 100 if rec is None else int(rec.recovery)


def _days_since(ctx: TrainerContext, muscle: str) -> float | None:
    rec = (ctx.recovery or {}).get(muscle)
    hours = getattr(rec, "hours_since", None) if rec is not None else None
    return None if hours is None else hours / 24


def _history(ctx: TrainerContext, exercise_id: int | None, name: str):
    if ctx.user_id is None:
        return []
    return load_history(ctx.user_id, exercise_key(exercise_id, name), now=ctx.now)


def _canonical(items) -> list[MuscleGroup]:
    wanted = set(items)
    return [m for m in CANONICAL if m in wanted]


def _equipment_list(keys) -> list:
    return sorted(k for k in keys if k != "bodyweight")[:4]


@dataclass(frozen=True)
class _Candidate:
    name: str
    primary: MuscleGroup
    secondary: tuple
    pattern: str
    equipment: frozenset
    compound: bool
    timed: bool
    cues: tuple
    exercise_id: int | None
    source: Literal["library", "catalog"]
    last_used: datetime | None = None


def _library_candidates(ctx: TrainerContext) -> list[_Candidate]:
    out = []
    for item in ctx.library:
        if item.primary is None:
            continue
        twin = find(item.name)
        out.append(
            _Candidate(
                name=item.name,
                primary=item.primary,
                secondary=item.secondary,
                pattern=item.pattern,
                equipment=item.equipment,
                compound=item.pattern in COMPOUND_PATTERNS,
                timed=item.timed,
                cues=twin.cues if twin else (),
                exercise_id=item.exercise_id,
                source="library",
                last_used=item.last_used,
            )
        )
    return out


def _catalog_candidate(item: CatalogExercise) -> _Candidate:
    return _Candidate(
        name=item.name,
        primary=item.primary,
        secondary=item.secondary,
        pattern=item.pattern,
        equipment=item.requires or frozenset({"bodyweight"}),
        compound=item.compound,
        timed=item.timed,
        cues=item.cues,
        exercise_id=None,
        source="catalog",
    )


def _doable(c: _Candidate, equipment: frozenset) -> bool:
    return set(c.equipment) - {"bodyweight"} <= set(equipment)


def _planned(
    c: _Candidate,
    ctx: TrainerContext,
    *,
    sets: int,
    rep_range: tuple[int, int],
    seconds: int,
    rest: int,
    warning: bool = False,
) -> PlannedExercise:
    """A candidate with targets from history (next_targets), else no weight."""
    rx = Prescription(
        sets=sets,
        rep_range=None if c.timed else rep_range,
        seconds=seconds if c.timed else None,
        pattern=c.pattern,
        equipment=frozenset(c.equipment),
    )
    result = next_targets(_history(ctx, c.exercise_id, c.name), rx, unit=ctx.unit, now=ctx.now)
    return PlannedExercise(
        name=c.name[:80],
        exercise_id=c.exercise_id,
        primary_muscle=c.primary,
        secondary_muscles=_canonical(m for m in c.secondary if m != c.primary)[:4],
        equipment=_equipment_list(c.equipment),
        movement_pattern=c.pattern,
        rest_seconds=max(0, min(600, rest)),
        sets=to_planned_sets(result)[:10],
        form_cues=[cue[:160] for cue in c.cues][:4],
        progression_rule=result.rule,
        progression_note=result.reason[:160],
        recovery_warning=warning,
        source=c.source,
    )


# ---------------------------------------------------------------------------
# Deterministic fallback plan (AI_COACH 8)
# ---------------------------------------------------------------------------
TEMPLATES: tuple[tuple[str, tuple[MuscleGroup, ...]], ...] = (
    ("Push", ("chest", "shoulders", "triceps")),
    ("Pull", ("back", "biceps")),
    ("Legs", ("quads", "hamstrings", "glutes", "calves")),
    ("Upper", ("chest", "back", "shoulders", "biceps", "triceps")),
    ("Lower", ("quads", "hamstrings", "glutes", "calves", "core")),
    ("Full body", ("chest", "back", "quads", "hamstrings", "shoulders", "core")),
)
BEGINNER_TEMPLATES = frozenset({"Full body", "Upper", "Lower"})

# goal -> ((compound sets, low, high, rest), (isolation sets, low, high, rest))
DOSE: dict[str, tuple[tuple[int, int, int, int], tuple[int, int, int, int]]] = {
    "strength": ((4, 4, 6, 150), (3, 8, 10, 90)),
    "muscle gain": ((3, 6, 10, 120), (3, 10, 12, 60)),
    "general fitness": ((3, 8, 12, 90), (3, 10, 12, 60)),
    "fat loss": ((3, 10, 12, 60), (3, 12, 15, 45)),
    "endurance": ((3, 12, 15, 45), (2, 15, 20, 30)),
}
HOLD_SECONDS = 30  # core holds: 3 × 30–45 s, prescribed at the low end


def _pick_template(ctx: TrainerContext) -> tuple[str, tuple[MuscleGroup, ...]] | None:
    """Highest-scoring template (AI_COACH 8 step 1); None means a recovery day."""
    if all((ctx.recovery or {}).get(m) is None or ctx.recovery[m].last_trained_at is None for m in CANONICAL):
        return TEMPLATES[-1]  # no history at all: full body
    best = None
    best_score = -math.inf
    for name, muscles in TEMPLATES:
        if ctx.experience == "beginner" and name not in BEGINNER_TEMPLATES:
            continue
        if any(_rec(ctx, m) < 50 for m in muscles[:2]):
            continue
        mean = sum(_rec(ctx, m) for m in muscles) / len(muscles)
        neglected = sum(1 for m in muscles if m in ctx.neglected)
        days = [_days_since(ctx, m) for m in muscles]
        stalest = max(7.0 if d is None else d for d in days)
        score = mean + 10 * neglected + 2 * min(7.0, stalest)
        if score > best_score:  # strict: ties go to the earlier template
            best, best_score = (name, muscles), score
    return best


def _order_muscles(ctx: TrainerContext, muscles) -> list[MuscleGroup]:
    index = {m: i for i, m in enumerate(CANONICAL)}
    return sorted(muscles, key=lambda m: (m not in ctx.neglected, -_rec(ctx, m), index[m]))


def _candidates_for(ctx: TrainerContext, muscle: str, library: list[_Candidate]) -> list[_Candidate]:
    """Per slot: library items with history (most recent first), other library items, catalog."""
    with_history = sorted(
        (c for c in library if c.primary == muscle and c.last_used is not None),
        key=lambda c: (-c.last_used.timestamp(), c.name.lower()),
    )
    without = sorted(
        (c for c in library if c.primary == muscle and c.last_used is None), key=lambda c: c.name.lower()
    )
    catalog = sorted(
        (_catalog_candidate(i) for i in CATALOG if i.primary == muscle), key=lambda c: c.name.lower()
    )
    return [c for c in with_history + without + catalog if _doable(c, ctx.equipment)]


def _dose(ctx: TrainerContext, compound: bool) -> tuple[int, tuple[int, int], int]:
    comp, iso = DOSE.get(ctx.goal, DOSE["general fitness"])
    sets, low, high, rest = comp if compound else iso
    if ctx.experience == "beginner":
        sets = max(2, sets - 1)
    elif ctx.experience == "advanced" and compound:
        sets = min(5, sets + 1)
    return sets, (low, high), rest


def _rationale(ctx: TrainerContext, muscles: list[MuscleGroup], template: str) -> str:
    if not any(ctx.recovery[m].last_trained_at for m in CANONICAL if m in (ctx.recovery or {})):
        return (
            f"A balanced {template.lower()} session to get started. "
            "Weights fill in from your logged sets as you train."
        )
    lead = muscles[0]
    days = _days_since(ctx, lead)
    text = f"{LABELS[lead]} is {_rec(ctx, lead)}% recovered"
    if days is None:
        text += " and hasn't been trained this week."
    else:
        whole = int(days)
        text += f" and you last trained it {whole} day{'s' if whole != 1 else ''} ago."
    neglected = [m for m in ctx.neglected if m in muscles and m != lead]
    if neglected:
        names = " and ".join(LABELS[m] for m in neglected[:2])
        verb = "haven't" if len(neglected[:2]) > 1 else "hasn't"
        text += f" {names} {verb} had much work lately."
    return text[:400]


def _recovery_day(ctx: TrainerContext, library: list[_Candidate]) -> TodayPlan:
    exercises = []
    for muscle in ("core", "calves"):
        options = _candidates_for(ctx, muscle, library)
        if options:
            exercises.append(
                _planned(options[0], ctx, sets=2, rep_range=(10, 15), seconds=HOLD_SECONDS, rest=45)
            )
    return TodayPlan(
        kind="recovery",
        title="Recovery day",
        focus_muscles=["core", "calves"],
        estimated_minutes=estimate_minutes(exercises),
        rationale="Most muscles are still recovering. Light core and calf work today.",
        exercises=exercises,
        source="fallback",
        notice=NOTICE_NO_AI,
    )


def fallback_plan(ctx: TrainerContext) -> TodayPlan:
    """Deterministic plan from recovery, history, library and the built-in catalog (no AI)."""
    library = _library_candidates(ctx)
    focus_list = FOCUS_MUSCLES.get(ctx.focus, [])
    if ctx.focus != "auto" and focus_list:
        template = ctx.focus.split(" (")[0].split(" / ")[0].capitalize()
        muscles = _order_muscles(ctx, focus_list)
    else:
        picked = _pick_template(ctx) if ctx.focus == "auto" else TEMPLATES[-1]
        if picked is None:
            return _recovery_day(ctx, library)
        template = picked[0]
        muscles = _order_muscles(ctx, picked[1])
        if ctx.focus == "auto":
            muscles = [m for m in muscles if _rec(ctx, m) >= 50] or muscles

    target = max(3, min(8, math.floor(ctx.minutes / 9 + 0.5)))
    chosen: list[tuple[_Candidate, bool]] = []
    used: set[str] = set()

    def take(muscle: str, prefer_compound: bool) -> bool:
        options = [c for c in _candidates_for(ctx, muscle, library) if normalize_name(c.name) not in used]
        preferred = [c for c in options if c.compound == prefer_compound]
        pick = (preferred or options or [None])[0]
        if pick is None:
            return False
        used.add(normalize_name(pick.name))
        chosen.append((pick, pick.compound))
        return True

    for muscle in muscles:  # first pass: a compound per muscle (isolation if none fits)
        if len(chosen) >= target:
            break
        take(muscle, True)
    while len(chosen) < target:  # second pass: isolation work, cycling through the muscles
        added = False
        for muscle in muscles:
            if len(chosen) >= target:
                break
            added = take(muscle, False) or added
        if not added:
            break

    exercises = []
    for cand, compound in chosen:
        sets, rep_range, rest = _dose(ctx, compound)
        seconds = HOLD_SECONDS
        exercises.append(_planned(cand, ctx, sets=sets, rep_range=rep_range, seconds=seconds, rest=rest))
    # Compounds first, then isolation, keeping the muscle order inside each group.
    exercises = [e for e, (_, c) in zip(exercises, chosen, strict=True) if c] + [
        e for e, (_, c) in zip(exercises, chosen, strict=True) if not c
    ]
    if not exercises:  # cannot happen with the catalog, but never return an invalid plan
        return _recovery_day(ctx, library)

    worked = []
    for ex in exercises:
        if ex.primary_muscle not in worked:
            worked.append(ex.primary_muscle)
    head = " & ".join(LABELS[m] for m in worked[:2])
    return TodayPlan(
        kind="workout",
        title=f"{template} · {head}"[:100],
        focus_muscles=worked[:6],
        estimated_minutes=estimate_minutes(exercises),
        rationale=_rationale(ctx, worked, template),
        exercises=exercises[:10],
        source="fallback",
        notice=NOTICE_NO_AI,
    )


# ---------------------------------------------------------------------------
# Finalizing AI output (AI_COACH 6.6)
# ---------------------------------------------------------------------------
def _focus_muscles(ctx: TrainerContext) -> set[str]:
    return set(FOCUS_MUSCLES.get(ctx.focus, []))


def _finalize_exercise(ai_ex: ai_coach.AIExercise, ctx: TrainerContext) -> PlannedExercise | None:
    """Steps 2-5 for one AI exercise. None when it must be dropped (equipment)."""
    needed = set(ai_ex.equipment) - {"bodyweight"}
    twin = find(ai_ex.name)
    if twin is not None:
        needed |= set(twin.requires)
    if not needed <= set(ctx.equipment):
        return None

    norm = normalize_name(ai_ex.name)
    match = next((i for i in ctx.library if normalize_name(i.name) == norm), None)
    exercise_id = match.exercise_id if match else None
    name = match.name if match else ai_ex.name
    source = "library" if match else "ai"

    sets = list(ai_ex.sets)
    rep_sets = [s for s in sets if s.target_reps is not None]
    timed = not rep_sets
    if not timed:
        sets = rep_sets
    warning = False
    if _rec(ctx, ai_ex.primary_muscle) < 50 and ai_ex.primary_muscle not in _focus_muscles(ctx):
        warning = True
        sets = sets[: min(len(sets), max(2, len(sets) - 1))]

    reps = [s.target_reps for s in sets if s.target_reps is not None]
    secs = [s.target_seconds for s in sets if s.target_seconds is not None]
    equipment = frozenset(ai_ex.equipment)
    rx = Prescription(
        sets=len(sets),
        rep_range=None if timed else (min(reps), max(reps)),
        seconds=min(secs) if timed else None,
        pattern=ai_ex.movement_pattern,
        equipment=equipment,
    )
    result = next_targets(_history(ctx, exercise_id, name), rx, unit=ctx.unit, now=ctx.now)

    if result.source == "history":
        planned_sets = to_planned_sets(result)
    elif result.source == "e1rm":
        planned_sets = [
            PlannedSet(target_reps=s.target_reps, weight=t.weight, unit=ctx.unit, weight_source="e1rm")
            for s, t in zip(sets, result.targets, strict=False)
        ]
    else:
        inc = increment(equipment, ctx.unit)
        planned_sets = []
        for s in sets:
            weight = None
            if s.weight is not None and 0 < s.weight <= 2000:
                weight = round_load(s.weight, inc, "nearest") or None
            planned_sets.append(
                PlannedSet(
                    target_reps=s.target_reps,
                    target_seconds=s.target_seconds if s.target_reps is None else None,
                    weight=weight,
                    unit=ctx.unit,
                    weight_source="ai" if weight is not None else "none",
                )
            )

    return PlannedExercise(
        name=name[:80],
        exercise_id=exercise_id,
        primary_muscle=ai_ex.primary_muscle,
        secondary_muscles=_canonical(m for m in ai_ex.secondary_muscles if m != ai_ex.primary_muscle)[:4],
        equipment=_equipment_list(ai_ex.equipment),
        movement_pattern=ai_ex.movement_pattern,
        rest_seconds=ai_ex.rest_seconds,
        sets=planned_sets[:10],
        form_cues=list(ai_ex.form_cues)[:4],
        progression_rule=result.rule,
        progression_note=result.reason[:160],
        recovery_warning=warning,
        source=source,
    )


def finalize_plan(ai: ai_coach.AITodayPlan, ctx: TrainerContext) -> TodayPlan:
    seen: set[str] = set()
    exercises: list[PlannedExercise] = []
    for ai_ex in ai.exercises:
        norm = normalize_name(ai_ex.name)
        if not norm or norm in seen:  # 1. dedupe
            continue
        seen.add(norm)
        planned = _finalize_exercise(ai_ex, ctx)  # 2-5
        if planned is not None:
            exercises.append(planned)
    exercises = exercises[:10]  # 6. caps
    if len(exercises) < 2:
        raise ai_coach.AIError("The coach's plan didn't fit your equipment. Here's a plan from your history.")
    return TodayPlan(
        kind="workout",
        title=ai.title or "Today's workout",
        focus_muscles=list(dict.fromkeys(ai.focus_muscles))[:6],
        estimated_minutes=estimate_minutes(exercises),  # 7. deterministic
        rationale=ai.rationale,
        exercises=exercises,
        source="ai",
        notice="",
    )


# ---------------------------------------------------------------------------
# Stored plans: GET / POST paths
# ---------------------------------------------------------------------------
def _owned_ids(user_id: int) -> set[int]:
    return {row[0] for row in db.session.query(Exercise.id).filter_by(user_id=user_id).all()}


def _sanitize_ids(plan: TodayPlan, user_id: int) -> TodayPlan:
    owned = _owned_ids(user_id)
    if all(ex.exercise_id is None or ex.exercise_id in owned for ex in plan.exercises):
        return plan
    fixed = [
        ex
        if ex.exercise_id is None or ex.exercise_id in owned
        else ex.model_copy(update={"exercise_id": None})
        for ex in plan.exercises
    ]
    return plan.model_copy(update={"exercises": fixed})


def _stored(user, day) -> tuple[TodayPlan | None, DailyPlan | None]:
    row = DailyPlan.query.filter_by(user_id=user.id, day=day).first()
    if row is None:
        return None, None
    plan = load_plan(row.plan_json)
    if plan is None:
        return None, row
    return _sanitize_ids(plan, user.id), row


def today_plan(user, *, now: datetime | None = None) -> tuple[TodayPlan, DailyPlan | None]:
    """GET path: today's stored plan if valid, else an UNSAVED fallback. Never calls the AI."""
    now = _now(now)
    plan, row = _stored(user, _local_day(user, now))
    if plan is not None:
        return plan, row
    return fallback_plan(build_context(user, now=now)), None


def generate_plan(
    user, *, minutes: int, focus: str, extra: str, use_ai: bool = True, now: datetime | None = None
) -> tuple[TodayPlan, DailyPlan]:
    """POST path: AI if configured, within budget and under the daily generation cap; otherwise the
    fallback with a notice. Upserts DailyPlan (no commit). A started plan is returned unchanged."""
    now = _now(now)
    day = _local_day(user, now)
    stored, row = _stored(user, day)
    if row is not None and row.session_id is not None and stored is not None:
        return stored, row

    ctx = build_context(user, now=now, minutes=minutes, focus=focus, extra=extra)
    plan: TodayPlan | None = None
    notice = NOTICE_NO_AI
    generations = (row.generations or 0) if row is not None else 0
    max_generations = int(current_app.config.get("AI_MAX_PLAN_GENERATIONS", 5))
    if use_ai and current_app.config.get("ANTHROPIC_API_KEY"):
        if generations >= max_generations:
            notice = NOTICE_MAX_GENERATIONS
        else:
            try:
                plan = finalize_plan(ai_coach.generate_today(ctx), ctx)
                generations += 1
            except ai_coach.AIBudgetError:
                notice = NOTICE_BUDGET
            except ai_coach.AIError:
                notice = NOTICE_AI_DOWN
    if plan is None:
        plan = fallback_plan(ctx).model_copy(update={"notice": notice})

    if row is None:
        row = DailyPlan(user_id=user.id, day=day)
        db.session.add(row)
    row.source = plan.source
    row.plan_json = plan.model_dump_json()
    row.generations = generations
    return plan, row


def ai_generations_left(row: DailyPlan | None) -> int:
    max_generations = int(current_app.config.get("AI_MAX_PLAN_GENERATIONS", 5))
    return max(0, max_generations - ((row.generations or 0) if row is not None else 0))


# ---------------------------------------------------------------------------
# Library rows for plan exercises
# ---------------------------------------------------------------------------
def _reps_label(sets: list[PlannedSet]) -> str:
    reps = [s.target_reps for s in sets if s.target_reps is not None]
    if reps:
        return str(min(reps)) if min(reps) == max(reps) else f"{min(reps)}-{max(reps)}"
    secs = [s.target_seconds for s in sets if s.target_seconds is not None]
    return f"{min(secs)}s" if secs else "10"


def _resolve_exercise(user, pex: PlannedExercise, owned: dict[int, Exercise], by_name: dict) -> Exercise:
    """The user's library row for a planned exercise, creating it (source ai/coach) if needed."""
    if pex.exercise_id is not None and pex.exercise_id in owned:
        return owned[pex.exercise_id]
    norm = normalize_name(pex.name)
    if norm in by_name:
        return by_name[norm]
    ex = Exercise(
        user_id=user.id,
        name=pex.name[:80],
        muscle_group=LABELS[pex.primary_muscle],
        equipment=", ".join(k.replace("_", " ") for k in pex.equipment)[:60] or "bodyweight",
        sets=max(1, min(20, len(pex.sets))),
        reps=_reps_label(pex.sets)[:20],
        rest_seconds=pex.rest_seconds,
        instructions=" ".join(pex.form_cues)[:2000],
        source="ai" if pex.source == "ai" else "coach",
    )
    db.session.add(ex)
    db.session.flush()
    ensure_muscles(
        ex, ai_map=MuscleMap(pex.primary_muscle, tuple(pex.secondary_muscles), pex.movement_pattern)
    )
    owned[ex.id] = ex
    by_name[norm] = ex
    return ex


def _library_maps(user) -> tuple[dict[int, Exercise], dict[str, Exercise]]:
    rows = Exercise.query.filter_by(user_id=user.id).order_by(Exercise.id).all()
    owned = {e.id: e for e in rows}
    by_name: dict[str, Exercise] = {}
    for e in rows:
        by_name.setdefault(normalize_name(e.name), e)
    return owned, by_name


def _rep_bounds(sets: list[PlannedSet]) -> tuple[int | None, int | None]:
    reps = [s.target_reps for s in sets if s.target_reps is not None]
    return (min(reps), max(reps)) if reps else (None, None)


def _apply_targets(plan_row: EntryPlan, pex: PlannedExercise, unit: str) -> None:
    low, high = _rep_bounds(pex.sets)
    plan_row.targets_json = dump_targets(pex.sets)
    plan_row.unit = pex.sets[0].unit if pex.sets else unit
    plan_row.rest_seconds = pex.rest_seconds
    plan_row.rep_low, plan_row.rep_high = low, high
    plan_row.pattern = pex.movement_pattern
    plan_row.progression_rule = (pex.progression_rule or "no_history")[:12]
    plan_row.progression_note = (pex.progression_note or "")[:160]


def _sync(entry: SessionEntry) -> None:
    from .sets import sync_entry

    sync_entry(entry)


def start_plan(user, *, now: datetime | None = None) -> WorkoutSession:
    """Turn today's plan into a tracked session (or return the open session unchanged)."""
    now = _now(now)
    open_session = WorkoutSession.query.filter_by(user_id=user.id, ended_at=None).first()
    if open_session is not None:
        return open_session

    plan, row = today_plan(user, now=now)
    day = _local_day(user, now)
    if row is None:  # an invalid stored row is overwritten, never duplicated (unique user+day)
        row = DailyPlan.query.filter_by(user_id=user.id, day=day).first()
    if row is None:
        row = DailyPlan(
            user_id=user.id, day=day, source=plan.source, plan_json=plan.model_dump_json(), generations=0
        )
        db.session.add(row)
    unit = unit_for(user.id)

    ws = WorkoutSession(user_id=user.id, day=day, started_at=now)
    db.session.add(ws)
    db.session.flush()

    owned, by_name = _library_maps(user)
    stored: list[PlannedExercise] = []
    for index, pex in enumerate(plan.exercises):
        ex = _resolve_exercise(user, pex, owned, by_name)
        entry = SessionEntry(session_id=ws.id, exercise_id=ex.id, exercise_name=ex.name, sets_done=0)
        db.session.add(entry)
        plan_row = EntryPlan(user_id=user.id, entry=entry, plan_index=index)
        _apply_targets(plan_row, pex, unit)
        db.session.add(plan_row)
        _sync(entry)
        stored.append(pex.model_copy(update={"exercise_id": ex.id}))

    row.source = plan.source
    row.plan_json = plan.model_copy(update={"exercises": stored}).model_dump_json()
    row.session_id = ws.id
    return ws


# ---------------------------------------------------------------------------
# Exercises added mid-session
# ---------------------------------------------------------------------------
def attach_targets(
    user,
    entry: SessionEntry,
    exercise: Exercise,
    *,
    sets: int,
    reps_text: str,
    weight_text: str,
    now: datetime | None = None,
) -> EntryPlan:
    """Targets for "Add exercise": history wins, else the typed reps/weight, else exercise.reps."""
    now = _now(now)
    unit = unit_for(user.id)
    count = max(1, min(20, int(sets or exercise.sets or 3)))
    mm = muscles_for(exercise)
    equipment = _exercise_equipment(exercise)

    rep_range = parse_rep_range(reps_text or "") or parse_rep_range(exercise.reps or "")
    seconds = None
    if rep_range is None:
        seconds = parse_seconds(reps_text or "") or parse_seconds(exercise.reps or "")
        if seconds is None:
            rep_range = (10, 10)
    rx = Prescription(
        sets=count, rep_range=rep_range, seconds=seconds, pattern=mm.pattern, equipment=equipment
    )
    history = load_history(user.id, exercise_key(exercise.id, exercise.name), now=now)
    result = next_targets(history, rx, unit=unit, now=now)

    if result.source in ("history", "e1rm"):
        targets = to_planned_sets(result)
    else:
        typed = parse_weight(weight_text or "", unit)
        weight = None
        if typed.value is not None and typed.value > 0:
            weight = round(from_lb(to_lb(typed.value, typed.unit), unit), 2)
        reps = parse_reps(reps_text or "") if rep_range else None
        targets = [
            PlannedSet(
                target_reps=None if rep_range is None else max(1, min(50, reps or rep_range[0])),
                target_seconds=None if rep_range is not None else max(5, min(600, seconds or 30)),
                weight=min(2000.0, weight) if weight is not None else None,
                unit=unit,
                weight_source="none",
            )
            for _ in range(count)
        ]

    low, high = _rep_bounds(targets)
    plan_row = EntryPlan(
        user_id=user.id,
        entry=entry,
        targets_json=dump_targets(targets),
        unit=unit,
        rest_seconds=max(0, min(600, exercise.rest_seconds or 0)),
        rep_low=low,
        rep_high=high,
        pattern=mm.pattern,
        progression_rule=result.rule[:12],
        progression_note=result.reason[:160],
    )
    db.session.add(plan_row)
    return plan_row


# ---------------------------------------------------------------------------
# Swaps (AI_COACH 7, BUILD_CONTRACT C9)
# ---------------------------------------------------------------------------
class SwapError(IndexError):
    """Swap not allowed (bad choice, no stored options, or sets already logged). Routes 404."""


def _entry_exercise(entry: SessionEntry) -> Exercise | None:
    if entry.exercise_id is None:
        return None
    ex = db.session.get(Exercise, entry.exercise_id)
    owner = entry.session.user_id if entry.session is not None else None
    return ex if ex is not None and ex.user_id == owner else None


def planned_from_entry(entry: SessionEntry) -> PlannedExercise:
    """The entry as a PlannedExercise (current targets), used as the original in swaps."""
    exercise = _entry_exercise(entry)
    mm = muscles_for(exercise, entry.exercise_name)
    plan_row = entry.plan
    targets = load_targets(plan_row.targets_json) if plan_row is not None else []
    if not targets:
        rep_range = parse_rep_range(entry.reps_done or "") or (10, 10)
        targets = [PlannedSet(target_reps=rep_range[0]) for _ in range(max(1, min(10, entry.sets_done or 3)))]
    name = (entry.exercise_name or "").strip()
    if len(name) < 2:
        name = f"Exercise {name}".strip()
    equipment = _exercise_equipment(exercise) if exercise is not None else equipment_keys(name)
    return PlannedExercise(
        name=name[:80],
        exercise_id=exercise.id if exercise is not None else None,
        primary_muscle=mm.primary or "core",
        secondary_muscles=list(mm.secondary)[:4],
        equipment=_equipment_list(equipment),
        movement_pattern=(plan_row.pattern if plan_row is not None else mm.pattern) or "other",
        rest_seconds=max(0, min(600, plan_row.rest_seconds if plan_row is not None else 90)),
        sets=targets[:10],
        progression_rule=(plan_row.progression_rule if plan_row is not None else "no_history")[:12],
        progression_note=(plan_row.progression_note if plan_row is not None else "")[:160],
        source="library" if exercise is not None else "ai",
    )


def _swap_shape(exercise: PlannedExercise) -> tuple[int, tuple[int, int], int]:
    reps = [s.target_reps for s in exercise.sets if s.target_reps is not None]
    secs = [s.target_seconds for s in exercise.sets if s.target_seconds is not None]
    rep_range = (min(reps), max(reps)) if reps else (8, 12)
    return len(exercise.sets), rep_range, (min(secs) if secs else HOLD_SECONDS)


def fallback_swaps(
    exercise: PlannedExercise, ctx: TrainerContext, *, reason: str, exclude: list[str], n: int = 3
) -> list[PlannedExercise]:
    """Deterministic swap options from the library and the catalog (AI_COACH 7.2)."""
    blocked = {normalize_name(x) for x in exclude} | {normalize_name(exercise.name)}
    original_equipment = set(exercise.equipment) - {"bodyweight"}
    pool: list[_Candidate] = []
    seen: set[str] = set()
    for cand in _library_candidates(ctx) + [_catalog_candidate(i) for i in CATALOG]:
        norm = normalize_name(cand.name)
        if cand.primary != exercise.primary_muscle or norm in blocked or norm in seen:
            continue
        if not _doable(cand, ctx.equipment):
            continue
        seen.add(norm)
        pool.append(cand)

    def score(c: _Candidate) -> int:
        value = 0
        if c.pattern == exercise.movement_pattern:
            value += -3 if reason == "discomfort" else 3
        if c.source == "library":
            value += 2
            if c.last_used is not None:
                value += 1
        if reason in ("busy", "no_equipment") and (set(c.equipment) & original_equipment):
            value -= 2
        if reason == "discomfort" and set(c.equipment) & {"machines", "cable_machine"}:
            value += 1
        return value

    pool.sort(key=lambda c: (-score(c), c.name.lower()))
    sets, rep_range, seconds = _swap_shape(exercise)
    return [
        _planned(c, ctx, sets=sets, rep_range=rep_range, seconds=seconds, rest=exercise.rest_seconds)
        for c in pool[: max(0, n)]
    ]


def _session_names(entry: SessionEntry) -> list[str]:
    session = entry.session
    names = [e.exercise_name for e in (session.entries if session is not None else []) if e.id != entry.id]
    return names + [entry.exercise_name]


def swap_options(
    user, entry: SessionEntry, *, reason: str, use_ai: bool, now: datetime | None = None
) -> tuple[list[PlannedExercise], str, str]:
    """(options <= 3, source "ai"|"fallback", notice). Stored on EntryPlan (no commit)."""
    now = _now(now)
    reason = reason if reason in dict(SWAP_REASONS) else "preference"
    original = planned_from_entry(entry)
    ctx = build_context(user, now=now)
    exclude = _session_names(entry)

    options: list[PlannedExercise] = []
    source, notice = "fallback", ""
    if use_ai and current_app.config.get("ANTHROPIC_API_KEY"):
        try:
            alternatives = ai_coach.suggest_swaps(original, ctx, reason=reason, exclude=exclude)
        except ai_coach.AIBudgetError:
            notice = NOTICE_SWAP_BUDGET
        except ai_coach.AIError:
            notice = NOTICE_SWAP_AI_DOWN
        else:
            blocked = {normalize_name(x) for x in exclude}
            for alt in alternatives:
                norm = normalize_name(alt.name)
                if not norm or norm in blocked:
                    continue
                planned = _finalize_exercise(alt, ctx)
                if planned is None:
                    continue
                blocked.add(norm)
                options.append(planned)
                if len(options) >= 3:
                    break
            if options:
                source = "ai"
    if len(options) < 3:
        options += fallback_swaps(
            original, ctx, reason=reason, exclude=exclude + [o.name for o in options], n=3 - len(options)
        )
    options = options[:3]

    if entry.plan is not None:
        entry.plan.swaps_json = dump_exercises(options)
        entry.plan.swaps_source = source
    return options, source, notice


def apply_swap(user, entry: SessionEntry, choice: int) -> SessionEntry:
    """Replace the entry's exercise with stored option ``choice`` (bounds-checked, 0 sets logged)."""
    plan_row = entry.plan
    if plan_row is None or entry.set_logs:
        raise SwapError("swap not allowed")
    options = load_exercises(plan_row.swaps_json)
    if not isinstance(choice, int) or not 0 <= choice < len(options):
        raise SwapError("no such option")
    option = options[choice]
    if option.exercise_id is not None and option.exercise_id not in _owned_ids(user.id):
        option = option.model_copy(update={"exercise_id": None})

    owned, by_name = _library_maps(user)
    ex = _resolve_exercise(user, option, owned, by_name)
    entry.exercise_id = ex.id
    entry.exercise_name = ex.name
    _apply_targets(plan_row, option, unit_for(user.id))
    plan_row.swaps_json = "[]"
    plan_row.swaps_source = None
    _sync(entry)

    # Keep today's stored plan in step, so the hero and "Next time" show the new exercise.
    daily = DailyPlan.query.filter_by(user_id=user.id, session_id=entry.session_id).first()
    if daily is not None and plan_row.plan_index is not None:
        plan = load_plan(daily.plan_json)
        if plan is not None and 0 <= plan_row.plan_index < len(plan.exercises):
            exercises = list(plan.exercises)
            exercises[plan_row.plan_index] = option.model_copy(update={"exercise_id": ex.id})
            daily.plan_json = plan.model_copy(update={"exercises": exercises}).model_dump_json()
    return entry
