"""Progressive overload (docs/AI_COACH.md section 4).

Deterministic and AI-free: parse what was logged, estimate a one-rep max (Epley), and
prescribe every set of the next session from the last one or two exposures.
All internal maths runs in lb; weights are rounded to real plate steps in the display unit.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from .muscles import KG_TO_LB, LOWER_COMPOUND, EquipmentKey, Pattern, normalize_name
from .plan_schema import PlannedSet

__all__ = ["KG_TO_LB"]  # re-exported; the rest is public by name

Unit = Literal["lb", "kg"]
Rating = Literal["easy", "ok", "hard"]

MAX_WEIGHT = 2000.0
MAX_REPS = 50  # PlannedSet.target_reps upper bound
MAX_SECONDS = 600
GAP_DAYS = 21


# ---------------------------------------------------------------------------
# 4.1 Parsing legacy free text
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ParsedWeight:
    value: float | None  # None = bodyweight / unknown
    unit: Unit
    bodyweight: bool


_BW_WORDS = {"", "bw", "bodyweight", "body weight", "body", "none", "-", "—", "n/a", "na"}
_WEIGHT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(kgs?|kilos?|kilograms?|lbs?|pounds?|#)?$")
_RANGE_RE = re.compile(r"^(\d+)\s*(?:-|–|—|to)\s*(\d+)")
_INT_RE = re.compile(r"^(\d+)\s*(?:x|reps?)?$")
_SECONDS_RE = re.compile(
    r"^(\d+(?:\.\d+)?)(?:\s*(?:-|–|—|to)\s*\d+(?:\.\d+)?)?\s*"
    r"(s|sec|secs|second|seconds|m|min|mins|minute|minutes)$"
)
_CLOCK_RE = re.compile(r"^(\d{1,2}):([0-5]\d)$")


def _unit(value: str | None, default: str = "lb") -> Unit:
    return "kg" if (value or default) == "kg" else "lb"


def parse_weight(text: str, default_unit: str = "lb") -> ParsedWeight:
    """ "135" -> 135 lb, "60kg" -> 60 kg, "bw"/"" -> bodyweight, "2x20" -> value None."""
    unit = _unit(default_unit)
    raw = (text or "").strip().lower().replace(",", ".")
    if raw in _BW_WORDS:
        return ParsedWeight(None, unit, True)
    match = _WEIGHT_RE.match(raw)
    if not match:
        return ParsedWeight(None, unit, False)
    value = min(MAX_WEIGHT, max(0.0, float(match.group(1))))
    suffix = match.group(2) or ""
    if suffix.startswith("k"):
        unit = "kg"
    elif suffix:
        unit = "lb"
    return ParsedWeight(value, unit, False)


def parse_reps(text: str) -> int | None:
    """ "10" -> 10, "8-10" -> 8 (conservative), "30s" -> None."""
    raw = (text or "").strip().lower()
    match = _RANGE_RE.match(raw)
    if match and not _SECONDS_RE.match(raw):
        value = min(int(match.group(1)), int(match.group(2)))
    else:
        match = _INT_RE.match(raw)
        if not match:
            return None
        value = int(match.group(1))
    return value if 0 <= value <= 100 else None


def parse_seconds(text: str) -> int | None:
    """ "30s"/"45 sec"/"1 min"/"1:30" -> 30/45/60/90. Ranges take the low end."""
    raw = (text or "").strip().lower()
    clock = _CLOCK_RE.match(raw)
    if clock:
        value = int(clock.group(1)) * 60 + int(clock.group(2))
    else:
        match = _SECONDS_RE.match(raw)
        if not match:
            return None
        value = float(match.group(1))
        if match.group(2).startswith("m"):
            value *= 60
        value = int(round(value))
    return value if 1 <= value <= MAX_SECONDS else None


def parse_rep_range(text: str) -> tuple[int, int] | None:
    """ "8-12" -> (8, 12), "10" -> (10, 10), "30s" -> None."""
    raw = (text or "").strip().lower()
    if _SECONDS_RE.match(raw) or _CLOCK_RE.match(raw):
        return None
    match = _RANGE_RE.match(raw)
    if match:
        low, high = sorted((int(match.group(1)), int(match.group(2))))
    else:
        match = _INT_RE.match(raw)
        if not match:
            return None
        low = high = int(match.group(1))
    if low < 1 or high > MAX_REPS:
        return None
    return low, high


def to_lb(value: float, unit: str) -> float:
    return value * KG_TO_LB if unit == "kg" else value


def from_lb(value_lb: float, unit: str) -> float:
    return value_lb / KG_TO_LB if unit == "kg" else value_lb


def _num(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def fmt_weight(value: float | None, unit: str) -> str:
    """135.0 -> "135 lb", 62.5 -> "62.5 kg", None -> ""."""
    if value is None:
        return ""
    return f"{_num(value)} {_unit(unit)}"


# ---------------------------------------------------------------------------
# 4.2 History
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PerformedSet:
    reps: int | None
    seconds: int | None
    weight_lb: float | None  # None = bodyweight
    rating: Rating | None
    target_reps: int | None
    target_seconds: int | None


@dataclass(frozen=True)
class Exposure:  # one exercise inside one session
    session_id: int
    performed_at: datetime  # UTC
    sets: tuple[PerformedSet, ...]


def exercise_key(exercise_id: int | None, name: str) -> str:
    return f"id:{exercise_id}" if exercise_id else f"name:{normalize_name(name)}"


def _entry_sets(entry, default_unit: str) -> list[PerformedSet]:
    """Tracked entries use their SetLogs; legacy entries expand by sets_done."""
    logs = list(getattr(entry, "set_logs", None) or [])
    if logs:
        out = []
        for log_row in logs:
            weight = None if log_row.weight is None else to_lb(float(log_row.weight), log_row.unit)
            rating = log_row.rating if log_row.rating in ("easy", "ok", "hard") else None
            out.append(
                PerformedSet(
                    reps=log_row.reps,
                    seconds=log_row.seconds,
                    weight_lb=weight,
                    rating=rating,
                    target_reps=log_row.target_reps,
                    target_seconds=log_row.target_seconds,
                )
            )
        return out
    if getattr(entry, "plan", None) is not None:
        return []  # tracked entry with nothing logged contributes nothing
    reps = parse_reps(entry.reps_done)
    seconds = None if reps is not None else parse_seconds(entry.reps_done)
    if reps is None and seconds is None:
        return []
    parsed = parse_weight(entry.weight, default_unit)
    weight = None if parsed.value is None else to_lb(parsed.value, parsed.unit)
    count = max(0, min(20, entry.sets_done or 0))
    return [PerformedSet(reps, seconds, weight, None, None, None)] * count


def _entry_time(entry, ws) -> datetime:
    logs = list(getattr(entry, "set_logs", None) or [])
    if logs:
        return max(row.logged_at for row in logs)
    return ws.ended_at or ws.started_at


def _skip_session(ws, now: datetime) -> bool:
    """The workout in progress (open and started within a day) must not shift targets mid-session."""
    return ws.ended_at is None and now - ws.started_at < timedelta(days=1)


def _rows(user_id: int, since: datetime):
    from ..models import SessionEntry, WorkoutSession

    return (
        SessionEntry.query.join(WorkoutSession, SessionEntry.session_id == WorkoutSession.id)
        .filter(WorkoutSession.user_id == user_id, WorkoutSession.started_at >= since)
        .order_by(WorkoutSession.started_at.desc(), SessionEntry.id)
        .all()
    )


def _library(user_id: int) -> dict:
    from ..models import Exercise

    return {e.id: e for e in Exercise.query.filter_by(user_id=user_id).all()}


def _default_unit(user_id: int) -> str:
    from .prefs import unit_for

    return unit_for(user_id)


def _entry_key(entry, library: dict) -> str:
    if entry.exercise_id and entry.exercise_id in library:
        return f"id:{entry.exercise_id}"
    return exercise_key(None, entry.exercise_name)


def _group(entries, *, now: datetime, default_unit: str, key_of) -> dict[str, list[Exposure]]:
    """Merge entries into Exposures (one per key and session), newest first."""
    buckets: dict[tuple[str, int], tuple[datetime, list[PerformedSet]]] = {}
    order: list[tuple[str, int]] = []
    for entry in entries:
        ws = entry.session
        if _skip_session(ws, now):
            continue
        key = key_of(entry)
        if key is None:
            continue
        sets = _entry_sets(entry, default_unit)
        if not sets:
            continue
        when = _entry_time(entry, ws)
        slot = (key, ws.id)
        if slot not in buckets:
            buckets[slot] = (when, [])
            order.append(slot)
        prev_when, prev_sets = buckets[slot]
        prev_sets.extend(sets)
        buckets[slot] = (max(prev_when, when), prev_sets)
    out: dict[str, list[Exposure]] = {}
    for key, sid in order:
        when, sets = buckets[(key, sid)]
        out.setdefault(key, []).append(Exposure(sid, when, tuple(sets)))
    for exposures in out.values():
        exposures.sort(key=lambda e: (e.performed_at, e.session_id), reverse=True)
    return out


def load_history(user_id: int, key: str, *, now: datetime, days: int = 120, limit: int = 4) -> list[Exposure]:
    """Newest-first exposures of one exercise key ("id:12" or "name:barbell bench press").

    An id key also matches entries whose exercise was deleted (exercise_id NULL) but whose
    normalized name equals the library exercise's name. Skips the session in progress.
    """
    library = _library(user_id)
    if key.startswith("id:"):
        try:
            ex_id = int(key[3:])
        except ValueError:
            return []
        ex = library.get(ex_id)
        if ex is None:
            return []
        wanted_name = normalize_name(ex.name)

        def key_of(entry):
            if entry.exercise_id == ex_id:
                return key
            if entry.exercise_id is None or entry.exercise_id not in library:
                return key if normalize_name(entry.exercise_name) == wanted_name else None
            return None
    else:
        wanted_name = key[5:] if key.startswith("name:") else normalize_name(key)

        def key_of(entry):
            return key if normalize_name(entry.exercise_name) == wanted_name else None

    entries = _rows(user_id, now - timedelta(days=days))
    grouped = _group(entries, now=now, default_unit=_default_unit(user_id), key_of=key_of)
    return grouped.get(key, [])[:limit]


def load_all_history(
    user_id: int, *, now: datetime, days: int = 180
) -> tuple[dict[str, list[Exposure]], dict[str, str], dict[str, int | None]]:
    """(exposures by key newest-first, display name by key, exercise_id by key). Feeds strength.py."""
    library = _library(user_id)
    entries = _rows(user_id, now - timedelta(days=days))
    grouped = _group(
        entries, now=now, default_unit=_default_unit(user_id), key_of=lambda e: _entry_key(e, library)
    )
    names: dict[str, str] = {}
    ids: dict[str, int | None] = {}
    for entry in entries:  # newest first, so the first name seen is the latest
        key = _entry_key(entry, library)
        if key not in grouped or key in names:
            continue
        if key.startswith("id:"):
            ids[key] = entry.exercise_id
            names[key] = library[entry.exercise_id].name
        else:
            ids[key] = None
            names[key] = entry.exercise_name
    return grouped, names, ids


# ---------------------------------------------------------------------------
# 4.3 Estimated 1RM (Epley)
# ---------------------------------------------------------------------------
def epley(weight_lb: float, reps: int) -> float | None:
    if weight_lb is None or reps is None or weight_lb <= 0:
        return None
    if reps == 1:
        return float(weight_lb)
    if 2 <= reps <= 12:
        return weight_lb * (1 + reps / 30)
    return None


def exposure_e1rm(exp: Exposure) -> float | None:
    values = [epley(s.weight_lb, s.reps) for s in exp.sets if s.weight_lb is not None and s.reps]
    values = [v for v in values if v is not None]
    return max(values) if values else None


def weight_for_reps(e1rm_lb: float, reps: int, rir: int = 2) -> float:
    return e1rm_lb / (1 + (reps + rir) / 30)


# ---------------------------------------------------------------------------
# 4.4 Rounding to real plates
# ---------------------------------------------------------------------------
INCREMENT_LB = {
    "barbell": 5,
    "smith_machine": 5,
    "dumbbells": 5,
    "machines": 5,
    "cable_machine": 5,
    "leg_press": 10,
    "kettlebells": 9,
    "_default": 5,
}
INCREMENT_KG = {
    "barbell": 2.5,
    "smith_machine": 2.5,
    "dumbbells": 2,
    "machines": 2.5,
    "cable_machine": 2.5,
    "leg_press": 5,
    "kettlebells": 4,
    "_default": 2.5,
}
_INCREMENT_PRIORITY = (
    "leg_press",
    "barbell",
    "smith_machine",
    "dumbbells",
    "kettlebells",
    "machines",
    "cable_machine",
)


def increment(equipment: frozenset[EquipmentKey], unit: str) -> float:
    table = INCREMENT_KG if unit == "kg" else INCREMENT_LB
    for key in _INCREMENT_PRIORITY:
        if key in (equipment or ()):
            return float(table[key])
    return float(table["_default"])


def round_load(value: float, inc: float, mode: Literal["nearest", "down"] = "nearest") -> float:
    if inc <= 0:
        return round(value, 2)
    steps = value / inc
    steps = math.floor(steps + 1e-6) if mode == "down" else math.floor(steps + 0.5)
    return round(steps * inc, 2)


# ---------------------------------------------------------------------------
# 4.5 Next-session targets
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Prescription:
    sets: int  # 1..20
    rep_range: tuple[int, int] | None  # None = timed
    seconds: int | None = None
    pattern: Pattern = "other"
    equipment: frozenset[EquipmentKey] = frozenset()


@dataclass(frozen=True)
class SetTarget:
    set_index: int
    reps: int | None
    seconds: int | None
    weight: float | None  # in `unit`, already rounded; None = bodyweight / unknown
    unit: Unit


RuleName = Literal[
    "no_history",
    "gap",
    "deload",
    "retry",
    "hard_drop",
    "hold",
    "easy_up",
    "add_weight",
    "add_reps",
    "rerange",
]


@dataclass(frozen=True)
class TargetResult:
    targets: tuple[SetTarget, ...]
    rule: RuleName
    reason: str  # one short sentence for the UI
    e1rm_lb: float | None
    source: Literal["history", "e1rm", "none"]


def _clamp_reps(value: int) -> int:
    return max(1, min(MAX_REPS, int(value)))


def _clamp_seconds(value: int) -> int:
    return max(5, min(MAX_SECONDS, int(value)))


def _working_weight(exp: Exposure) -> float | None:
    """Most frequent weight among the sets (ties go to the heavier); None = bodyweight."""
    counts = Counter(s.weight_lb for s in exp.sets)
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], -1.0 if kv[0] is None else kv[0]))[0]


def _missed(exp: Exposure, low: int | None) -> bool:
    for s in exp.sets:
        if s.reps is not None:
            want = s.target_reps or low
            if want is not None and s.reps < want:
                return True
        elif s.seconds is not None and s.target_seconds is not None and s.seconds < s.target_seconds:
            return True
    return False


def _rating(exp: Exposure) -> Rating:
    rated = [s.rating for s in exp.sets if s.rating]
    if "hard" in rated:
        return "hard"
    if rated and all(r == "easy" for r in rated):
        return "easy"
    return "ok"


def _expand(values: list, count: int) -> list:
    """Per-set values for `count` sets: extra sets reuse the last value."""
    if not values:
        return []
    return [values[i] if i < len(values) else values[-1] for i in range(count)]


def _summary(exp: Exposure, weight: float | None, unit: str) -> str:
    reps = [s.reps for s in exp.sets if s.reps is not None]
    secs = [s.seconds for s in exp.sets if s.seconds is not None]
    if reps:
        body = f"{len(exp.sets)}×{reps[0]}" if len(set(reps)) == 1 else ", ".join(map(str, reps)) + " reps"
    elif secs:
        body = f"{len(exp.sets)}×{secs[0]}s" if len(set(secs)) == 1 else ", ".join(f"{s}s" for s in secs)
    else:
        body = f"{len(exp.sets)} sets"
    if weight is None:
        return f"{body} bodyweight"
    return f"{body} @ {fmt_weight(weight, unit)}"


def _signed(delta: float, unit: str) -> str:
    return ("+" if delta >= 0 else "−") + fmt_weight(abs(delta), unit)


def next_targets(history: list[Exposure], rx: Prescription, *, unit: str, now: datetime) -> TargetResult:
    """Prescribe every set of the next session (rule table in AI_COACH 4.5, first match wins)."""
    unit = _unit(unit)
    count = max(1, min(20, rx.sets))
    timed = rx.rep_range is None
    low, high = rx.rep_range if rx.rep_range else (None, None)
    base_seconds = _clamp_seconds(rx.seconds or 30)
    inc = increment(rx.equipment, unit)

    def build(reps: list[int | None], seconds: list[int | None], weight: float | None) -> tuple:
        if weight is not None and weight <= 0:
            weight = None
        if weight is not None:
            weight = min(MAX_WEIGHT, weight)
        out = []
        for i in range(count):
            r = reps[i] if i < len(reps) else None
            sec = seconds[i] if i < len(seconds) else None
            if timed:
                out.append(SetTarget(i, None, _clamp_seconds(sec or base_seconds), weight, unit))
            else:
                out.append(SetTarget(i, _clamp_reps(r or low), None, weight, unit))
        return tuple(out)

    history = [h for h in history if h.sets]
    if not history:
        if timed:
            reason = f"No history yet: hold for {base_seconds} seconds with good form."
        else:
            reason = f"No history yet: pick a weight you can lift for {low} clean reps."
        return TargetResult(build([], [], None), "no_history", reason, None, "none")

    last = history[0]
    prev = history[1] if len(history) > 1 else None
    w_lb = _working_weight(last)
    w = None if w_lb is None else round(float(from_lb(w_lb, unit)), 2)
    e1rm = exposure_e1rm(last)
    rating = _rating(last)
    missed = _missed(last, low)
    summary = _summary(last, w, unit)

    def scaled(factor: float, mode: Literal["nearest", "down"]) -> float | None:
        """W × factor rounded; a change is always at least one increment."""
        if w is None:
            return None
        new = round_load(w * factor, inc, mode)
        if factor > 1 and new <= w:
            new = round_load(w, inc, "nearest") + inc
            new = new if new > w else new + inc
        if factor < 1 and new >= w:
            new = round_load(w, inc, "nearest") - inc
            new = new if new < w else new - inc
        new = round(new, 2)
        return new if new > 0 else None

    def result(rule, reason, reps=(), seconds=(), weight=None, source="history") -> TargetResult:
        return TargetResult(build(list(reps), list(seconds), weight), rule, reason[:160], e1rm, source)

    # Rule 1: long gap.
    if now - last.performed_at > timedelta(days=GAP_DAYS):
        days = (now - last.performed_at).days
        new = scaled(0.90, "down")
        tail = f"restart at {fmt_weight(new, unit)}." if new is not None else "ease back in."
        if timed:
            secs = _expand([s.seconds or base_seconds for s in last.sets], count)
            return result("gap", f"{days} days since last time: {tail}", seconds=secs, weight=new)
        return result("gap", f"{days} days since last time: {tail}", reps=[low] * count, weight=new)

    # Timed holds/carries: progress the seconds, keep the load.
    if timed:
        secs = _expand([s.seconds or s.target_seconds or base_seconds for s in last.sets], count)
        if missed:
            return result("retry", f"Last time {summary}: same targets again.", seconds=secs, weight=w)
        if rating == "hard":
            return result("hold", f"Last time {summary} felt hard: hold steady.", seconds=secs, weight=w)
        step = 10 if rating == "easy" else 5
        secs = [min(MAX_SECONDS, s + step) for s in secs]
        rule = "easy_up" if rating == "easy" else "add_reps"
        verb = "felt easy" if rating == "easy" else "went well"
        return result(rule, f"Last time {summary} {verb}: +{step} s per set.", seconds=secs, weight=w)

    achieved = [s.reps if s.reps is not None else (s.target_reps or low) for s in last.sets]
    targets_last = [s.target_reps or low for s in last.sets]

    # Rule 2: missed twice in a row.
    if w is not None and missed and prev is not None and _missed(prev, low):
        new = scaled(0.90, "down")
        return result(
            "deload", f"Missed reps twice: deload to {fmt_weight(new, unit)}.", reps=[low] * count, weight=new
        )

    # Re-range: the plan's rep range is far from what was done last time.
    ref = max(targets_last + achieved)
    if w is not None and e1rm is not None and (ref < low - 2 or ref > high + 2):
        new_lb = weight_for_reps(e1rm, low)
        new = round_load(from_lb(new_lb, unit), inc, "down")
        new = new if new > 0 else None
        return TargetResult(
            build([low] * count, [], new),
            "rerange",
            f"New rep range: {low} reps at {fmt_weight(new, unit)} from your estimated max.",
            e1rm,
            "e1rm",
        )

    # Rule 3: missed last time only.
    if missed:
        return result(
            "retry",
            f"Missed reps last time ({summary}): same targets again.",
            reps=_expand(targets_last, count),
            weight=w,
        )

    capped = [min(high, r) for r in achieved]

    if w is None:  # bodyweight: progress reps only
        if rating == "hard":
            return result("hold", f"Last time {summary} felt hard: same reps.", reps=_expand(achieved, count))
        step = 2 if rating == "easy" else 1
        reps = [r + step for r in achieved]
        rule = "easy_up" if rating == "easy" else "add_reps"
        reason = f"Last time {summary}: +{step} rep{'s' if step > 1 else ''} per set."
        if max(reps) > high + 5:
            reason = f"Last time {summary}: ready for a harder variation or added weight."
        return result(rule, reason, reps=_expand(reps, count))

    # Rule 4 / 5: hard.
    if rating == "hard" and prev is not None and _rating(prev) == "hard":
        new = scaled(0.95, "down")
        return result(
            "hard_drop",
            f"Hard two sessions in a row: drop to {fmt_weight(new, unit)}.",
            reps=_expand(achieved, count),
            weight=new,
        )
    if rating == "hard":
        return result(
            "hold", f"Last time {summary} felt hard: hold the weight.", reps=_expand(capped, count), weight=w
        )

    # Rule 6: easy.
    if rating == "easy":
        factor = 1.05 if rx.pattern in LOWER_COMPOUND else 1.025
        new = scaled(factor, "nearest")
        return result(
            "easy_up",
            f"Last time {summary} felt easy: {_signed((new or 0) - w, unit)}.",
            reps=_expand(capped, count),
            weight=new,
        )

    # Rule 7: ok and at the top of the range.
    if all(r >= high for r in achieved):
        new = round(w + inc, 2)
        return result(
            "add_weight",
            f"Hit {high} reps on every set: +{fmt_weight(inc, unit)}, back to {low} reps.",
            reps=[low] * count,
            weight=new,
        )

    # Rule 8: ok, not at the top yet.
    reps = [min(high, r + 1) for r in achieved]
    return result(
        "add_reps", f"Last time {summary}: one more rep per set.", reps=_expand(reps, count), weight=w
    )


def to_planned_sets(result: TargetResult) -> list[PlannedSet]:
    source = {"history": "history", "e1rm": "e1rm"}.get(result.source, "none")
    return [
        PlannedSet(
            target_reps=t.reps,
            target_seconds=t.seconds if t.reps is None else None,
            weight=t.weight,
            unit=t.unit,
            weight_source=source,
        )
        for t in result.targets[:20]
    ]
