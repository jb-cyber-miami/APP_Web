"""Shared, validated shapes for the AI coach (docs/BUILD_CONTRACT.md section 2.2).

Everything that is stored as JSON (``DailyPlan.plan_json``, ``EntryPlan.targets_json`` and
``EntryPlan.swaps_json``) is one of the pydantic models below. It is validated before it is
stored and re-validated every time it is loaded, so a tampered or stale row can never reach a
template or a query unchecked.

``TrainerContext`` and its helper dataclasses live here (not in ``trainer.py``) so that
``ai_coach`` can build prompts from them without importing ``trainer``.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, TypeAdapter, ValidationError, model_validator

from .muscles import EquipmentKey, MuscleGroup, Pattern

log = logging.getLogger(__name__)

Unit = Literal["lb", "kg"]


# ---------------------------------------------------------------------------
# Plan models (stored JSON)
# ---------------------------------------------------------------------------
class PlannedSet(BaseModel):
    target_reps: int | None = Field(default=None, ge=1, le=50)
    target_seconds: int | None = Field(default=None, ge=5, le=600)
    weight: float | None = Field(default=None, ge=0, le=2000)  # in `unit`, rounded to plates
    unit: Unit = "lb"
    weight_source: Literal["history", "e1rm", "ai", "none"] = "none"

    @model_validator(mode="after")
    def _one_kind(self):
        if (self.target_reps is None) == (self.target_seconds is None):
            raise ValueError("exactly one of target_reps / target_seconds")
        return self

    @property
    def timed(self) -> bool:
        return self.target_seconds is not None


class PlannedExercise(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    exercise_id: int | None = None  # library row; ownership re-checked whenever it is used
    primary_muscle: MuscleGroup
    secondary_muscles: list[MuscleGroup] = Field(default_factory=list, max_length=4)
    equipment: list[EquipmentKey] = Field(default_factory=list, max_length=4)
    movement_pattern: Pattern = "other"
    rest_seconds: int = Field(ge=0, le=600)
    sets: list[PlannedSet] = Field(min_length=1, max_length=10)
    form_cues: list[Annotated[str, StringConstraints(max_length=160)]] = Field(
        default_factory=list, max_length=4
    )
    progression_rule: str = Field(default="no_history", max_length=12)
    progression_note: str = Field(default="", max_length=160)
    recovery_warning: bool = False
    source: Literal["ai", "library", "catalog"] = "ai"


class TodayPlan(BaseModel):
    schema_version: Literal[1] = 1
    kind: Literal["workout", "recovery"] = "workout"
    title: str = Field(max_length=100)
    focus_muscles: list[MuscleGroup] = Field(default_factory=list, max_length=6)
    estimated_minutes: int = Field(ge=5, le=180)
    rationale: str = Field(default="", max_length=400)
    exercises: list[PlannedExercise] = Field(min_length=1, max_length=10)
    source: Literal["ai", "fallback"]
    notice: str = Field(default="", max_length=200)

    @property
    def total_sets(self) -> int:
        return sum(len(ex.sets) for ex in self.exercises)


# ---------------------------------------------------------------------------
# Row view (hero card, swap page, summary "Next time")
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlanRowView:
    name: str
    sets: int
    reps_text: str  # "8", "8–12", "30s" (en dash for ranges)
    weight: float | None  # display unit; None => "" (bodyweight / unknown)
    unit: str
    weight_label: str  # "" or "suggested" (weight_source == "ai")
    note: str  # progression_note
    warning: bool  # recovery_warning
    primary: MuscleGroup
    delta: float | None = None  # only used by "Next time" rows


def range_text(low: int, high: int, suffix: str = "") -> str:
    return f"{low}{suffix}" if low == high else f"{low}–{high}{suffix}"


def sets_reps_text(sets: list[PlannedSet]) -> str:
    """ "8", "8–12" or "30s" / "30–45s" for a list of planned sets."""
    reps = [s.target_reps for s in sets if s.target_reps is not None]
    if reps:
        return range_text(min(reps), max(reps))
    secs = [s.target_seconds for s in sets if s.target_seconds is not None]
    if secs:
        return range_text(min(secs), max(secs), "s")
    return ""


def plan_row_view(ex: PlannedExercise) -> PlanRowView:
    weights = [s.weight for s in ex.sets if s.weight is not None]
    first = ex.sets[0]
    ai_weight = any(s.weight is not None and s.weight_source == "ai" for s in ex.sets)
    return PlanRowView(
        name=ex.name,
        sets=len(ex.sets),
        reps_text=sets_reps_text(ex.sets),
        weight=max(weights) if weights else None,
        unit=first.unit,
        weight_label="suggested" if ai_weight else "",
        note=ex.progression_note,
        warning=ex.recovery_warning,
        primary=ex.primary_muscle,
    )


# ---------------------------------------------------------------------------
# Safe (de)serialisation
# ---------------------------------------------------------------------------
_TARGETS = TypeAdapter(list[PlannedSet])
_EXERCISES = TypeAdapter(list[PlannedExercise])


def load_targets(text: str) -> list[PlannedSet]:
    """Parse ``EntryPlan.targets_json``. Returns ``[]`` on any error (never raises)."""
    try:
        items = _TARGETS.validate_json(text or "[]")
    except (ValidationError, ValueError):
        log.warning("Discarding invalid stored set targets")
        return []
    return items[:20]


def dump_targets(targets: list[PlannedSet]) -> str:
    return json.dumps([t.model_dump(mode="json") for t in targets[:20]])


def load_exercises(text: str) -> list[PlannedExercise]:
    """Parse ``EntryPlan.swaps_json`` (or any list of exercises). ``[]`` on any error."""
    try:
        return _EXERCISES.validate_json(text or "[]")
    except (ValidationError, ValueError):
        log.warning("Discarding invalid stored exercise list")
        return []


def dump_exercises(exercises: list[PlannedExercise]) -> str:
    return json.dumps([e.model_dump(mode="json") for e in exercises])


def load_plan(text: str) -> TodayPlan | None:
    """Parse ``DailyPlan.plan_json``. ``None`` on any error (the caller treats it as no plan)."""
    try:
        return TodayPlan.model_validate_json(text or "")
    except (ValidationError, ValueError):
        log.warning("Discarding invalid stored daily plan")
        return None


# ---------------------------------------------------------------------------
# Session length (AI_COACH 6.6 step 7)
# ---------------------------------------------------------------------------
WORK_SECONDS_PER_SET = 40
SECONDS_PER_EXERCISE = 60


def estimate_minutes(exercises: list[PlannedExercise]) -> int:
    """ceil(Σ(sets × (40 s or target_seconds) + sets × rest + 60 s per exercise) / 60), 5..180."""
    total = 0
    for ex in exercises:
        for s in ex.sets:
            total += (s.target_seconds or WORK_SECONDS_PER_SET) + ex.rest_seconds
        total += SECONDS_PER_EXERCISE
    return max(5, min(180, math.ceil(total / 60)))


# ---------------------------------------------------------------------------
# Trainer context (AI_COACH 6.1). Built by trainer.build_context; read by ai_coach prompts.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LibraryItem:
    exercise_id: int
    name: str  # user text: sanitized before it reaches a prompt
    primary: MuscleGroup | None
    equipment: frozenset[EquipmentKey]
    secondary: tuple[MuscleGroup, ...] = ()
    pattern: Pattern = "other"
    timed: bool = False  # the library row's reps look like "30s"
    last_used: datetime | None = None  # UTC; None = no logged history


@dataclass(frozen=True)
class RecentExercise:
    name: str
    primary: MuscleGroup | None
    summary: str  # "4×8 @ 185 lb (hard)" or "3×12 bodyweight"


@dataclass(frozen=True)
class RecentSession:
    day: date
    exercises: tuple[RecentExercise, ...]


@dataclass(frozen=True)
class TrainerContext:
    today: date  # user's timezone
    now: datetime  # naive UTC
    gym_size: str
    equipment: frozenset[EquipmentKey]  # incl. bodyweight
    goal: str
    experience: str
    notes: str  # GymProfile.notes (<=500), user text
    unit: Unit
    minutes: int  # 10..120
    focus: str  # "auto" or a FOCUS key from forms.py
    extra: str  # PlanGenerateForm.extra (<=300), user text
    recovery: dict[str, Any]  # recovery.RecoveryMap (MuscleRecovery values)
    neglected: list[MuscleGroup]
    recent: list[RecentSession]  # newest first, <= 40 lines in total
    library: list[LibraryItem]  # <= 60: recently used first, then name
    user_id: int | None = None  # for history lookups and the AI budget; None in pure tests
