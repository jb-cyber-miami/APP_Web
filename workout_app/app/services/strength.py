"""Strength Index (docs/AI_COACH.md section 5, formula verbatim).

Self-relative: 100 = the lift's baseline (best e1RM in the first 14 days after its first
exposure); 120 = 20 % stronger. ``compute_strength`` is pure; ``strength_report`` loads history.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..models import utcnow
from .muscles import CANONICAL, LABELS, MuscleGroup, library_maps, map_exercise
from .progression import Exposure, epley, fmt_weight, from_lb, load_all_history

BASELINE_WINDOW = timedelta(days=14)
CURRENT_WINDOW = timedelta(days=28)
EXCLUDE_AFTER = timedelta(days=90)
WEIGHT_WINDOW = timedelta(days=56)
TREND = timedelta(days=28)
MIN_GROUPS = 3
MIN_EXPOSURES = 2


@dataclass(frozen=True)
class LiftScore:
    key: str
    exercise_id: int | None
    name: str
    muscle: MuscleGroup
    baseline_e1rm_lb: float
    current_e1rm_lb: float
    index: int
    stale: bool
    pr: bool
    best_set: str  # "185 lb × 8" in the user's unit
    current_e1rm: float  # display unit, 1 decimal


@dataclass(frozen=True)
class MuscleScore:
    muscle: MuscleGroup
    index: int
    delta_28d: int | None
    lifts: int


@dataclass(frozen=True)
class StrengthReport:
    overall: int | None
    overall_delta_28d: int | None
    muscles: dict[MuscleGroup, MuscleScore]  # only groups with data
    lifts: list[LiftScore]  # by muscle (canonical), then -current_e1rm


def _round(x: float) -> int:
    return int(math.floor(x + 0.5))


def _set_e1rm(s) -> float | None:
    if s.weight_lb is None or s.weight_lb <= 0 or s.reps is None:
        return None
    return epley(s.weight_lb, s.reps)


def _best(exp: Exposure):
    """(e1rm_lb, best set) of an exposure, or (None, None)."""
    best, best_set = None, None
    for s in exp.sets:
        v = _set_e1rm(s)
        if v is not None and (best is None or v > best):
            best, best_set = v, s
    return best, best_set


def _display(value_lb: float, unit: str) -> float:
    return round(from_lb(value_lb, unit), 1)


def _best_set_text(s, unit: str) -> str:
    if s is None:
        return ""
    return f"{fmt_weight(_display(s.weight_lb, unit), unit)} × {s.reps}"


def _working_sets(exps: list[Exposure], since: datetime, as_of: datetime) -> int:
    return sum(1 for e in exps if since < e.performed_at <= as_of for s in e.sets if _set_e1rm(s) is not None)


def _lifts(exposures_by_key, names, muscles, exercise_ids, as_of: datetime, unit: str):
    """LiftScores as of ``as_of`` plus the working-set weight of each lift."""
    out: list[tuple[LiftScore, int]] = []
    for key, raw in exposures_by_key.items():
        muscle = muscles.get(key)
        if muscle not in CANONICAL:
            continue
        exps = sorted((e for e in raw if e.performed_at <= as_of), key=lambda e: e.performed_at)
        scored = [(e, *_best(e)) for e in exps]
        scored = [t for t in scored if t[1] is not None]
        if not scored:
            continue
        latest_exp, latest_val, _ = scored[-1]
        if latest_exp.performed_at < as_of - EXCLUDE_AFTER:
            continue
        first_at = scored[0][0].performed_at
        baseline = max(v for e, v, _ in scored if e.performed_at <= first_at + BASELINE_WINDOW)
        recent = [t for t in scored if t[0].performed_at > as_of - CURRENT_WINDOW]
        stale = not recent
        pool = recent or [scored[-1]]
        _, current, current_set = max(pool, key=lambda t: t[1])
        earlier = [v for _, v, _ in scored[:-1]]
        pr = bool(earlier) and latest_val > max(earlier)
        weight = max(1, _working_sets(exps, as_of - WEIGHT_WINDOW, as_of))
        out.append(
            (
                LiftScore(
                    key=key,
                    exercise_id=(exercise_ids or {}).get(key),
                    name=names.get(key, key),
                    muscle=muscle,
                    baseline_e1rm_lb=round(baseline, 1),
                    current_e1rm_lb=round(current, 1),
                    index=_round(100 * current / baseline) if baseline > 0 else 100,
                    stale=stale,
                    pr=pr,
                    best_set=_best_set_text(current_set, unit),
                    current_e1rm=_display(current, unit),
                ),
                weight,
            )
        )
    return out


def _muscle_indices(lifts) -> dict[str, tuple[int, int]]:
    """muscle -> (weighted index, number of lifts)."""
    acc: dict[str, list[float]] = {}
    for lift, weight in lifts:
        a = acc.setdefault(lift.muscle, [0.0, 0.0, 0])
        a[0] += lift.index * weight
        a[1] += weight
        a[2] += 1
    return {m: (_round(a[0] / a[1]), int(a[2])) for m, a in acc.items()}


def _overall(indices: dict[str, tuple[int, int]], exposures: int) -> int | None:
    if len(indices) < MIN_GROUPS or exposures < MIN_EXPOSURES:
        return None
    return _round(sum(i for i, _ in indices.values()) / len(indices))


def _exposure_count(exposures_by_key, keys, as_of: datetime) -> int:
    return sum(1 for k in keys for e in exposures_by_key.get(k, []) if e.performed_at <= as_of)


def compute_strength(
    exposures_by_key: dict[str, list[Exposure]],
    names: dict[str, str],
    muscles: dict[str, MuscleGroup],
    *,
    as_of: datetime,
    exercise_ids: dict[str, int | None] | None = None,
    unit: str = "lb",
) -> StrengthReport:
    """Pure Strength Index computation (AI_COACH 5.1)."""
    lifts = _lifts(exposures_by_key, names, muscles, exercise_ids, as_of, unit)
    indices = _muscle_indices(lifts)
    overall = _overall(indices, _exposure_count(exposures_by_key, [lf.key for lf, _ in lifts], as_of))

    before = as_of - TREND
    lifts_before = _lifts(exposures_by_key, names, muscles, exercise_ids, before, unit)
    indices_before = _muscle_indices(lifts_before)
    overall_before = _overall(
        indices_before, _exposure_count(exposures_by_key, [lf.key for lf, _ in lifts_before], before)
    )

    scores: dict[MuscleGroup, MuscleScore] = {}
    for m in CANONICAL:
        if m not in indices:
            continue
        index, count = indices[m]
        prev = indices_before.get(m)
        scores[m] = MuscleScore(
            muscle=m, index=index, delta_28d=index - prev[0] if prev else None, lifts=count
        )
    order = {m: i for i, m in enumerate(CANONICAL)}
    ordered = sorted(
        (lf for lf, _ in lifts), key=lambda lf: (order[lf.muscle], -lf.current_e1rm_lb, lf.name.lower())
    )
    return StrengthReport(
        overall=overall,
        overall_delta_28d=(
            overall - overall_before if overall is not None and overall_before is not None else None
        ),
        muscles=scores,
        lifts=ordered,
    )


def strength_report(user_id: int, *, now: datetime | None = None, unit: str = "lb") -> StrengthReport:
    now = now or utcnow()
    exposures, names, ids = load_all_history(user_id, now=now)
    maps = library_maps(user_id)
    muscles: dict[str, MuscleGroup] = {}
    for key in exposures:
        ex_id = ids.get(key)
        mm = maps.get(ex_id) if ex_id is not None else None
        if mm is None:
            mm = map_exercise(names.get(key, ""), "")
        if mm.primary is not None:
            muscles[key] = mm.primary
    return compute_strength(exposures, names, muscles, as_of=now, exercise_ids=ids, unit=unit)


# ---------------------------------------------------------------------------
# View helpers
# ---------------------------------------------------------------------------
def index_pct(index: int | None) -> int:
    """Ring/bar fill: baseline 100 fills ~67 %, 150+ fills it."""
    if index is None:
        return 0
    return max(0, min(100, _round(index * 2 / 3)))


@dataclass(frozen=True)
class StrengthRow:
    muscle: MuscleGroup
    label: str
    index: int | None
    pct: int
    delta_28d: int | None
    lifts: int


def strength_rows(report: StrengthReport) -> list[StrengthRow]:
    rows = []
    for m in CANONICAL:
        score = report.muscles.get(m)
        index = score.index if score else None
        rows.append(
            StrengthRow(
                muscle=m,
                label=LABELS[m],
                index=index,
                pct=index_pct(index),
                delta_28d=score.delta_28d if score else None,
                lifts=score.lifts if score else 0,
            )
        )
    return rows


def sparkline_points(values: list[float], *, width: int = 100, height: int = 32, pad: int = 2) -> str | None:
    """SVG polyline points "x,y x,y …" (1-decimal numbers only). Oldest -> newest, left -> right."""
    clean: list[float] = []
    for v in values or []:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            clean.append(f)
    if len(clean) < 2:
        return None
    lo, hi = min(clean), max(clean)
    span_x = max(0, width - 2 * pad)
    span_y = max(0, height - 2 * pad)
    step = span_x / (len(clean) - 1)
    points = []
    for i, v in enumerate(clean):
        x = pad + i * step
        y = height / 2 if hi == lo else pad + (hi - v) / (hi - lo) * span_y
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)
