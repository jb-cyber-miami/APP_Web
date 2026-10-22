"""Muscle taxonomy, movement patterns and equipment keys (docs/AI_COACH.md section 2).

This module is the single home of every shared coach constant. It is pure apart from
``muscles_for`` / ``ensure_muscles``, which read or add an ``ExerciseMuscles`` row (no commit).
It imports nothing from ``forms.py``; ``tests/test_muscles.py`` ties the two together.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, get_args

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..models import Exercise, ExerciseMuscles

# ---------------------------------------------------------------------------
# Canonical groups, patterns, equipment, ratings, units
# ---------------------------------------------------------------------------
MuscleGroup = Literal[
    "chest",
    "back",
    "shoulders",
    "biceps",
    "triceps",
    "forearms",
    "core",
    "quads",
    "hamstrings",
    "glutes",
    "calves",
]
CANONICAL: tuple[MuscleGroup, ...] = get_args(MuscleGroup)
LARGE: frozenset[str] = frozenset({"chest", "back", "quads", "hamstrings", "glutes"})
LABELS: dict[str, str] = {m: m.title() for m in CANONICAL}

Pattern = Literal[
    "horizontal_push",
    "vertical_push",
    "horizontal_pull",
    "vertical_pull",
    "squat",
    "hinge",
    "lunge",
    "isolation_upper",
    "isolation_lower",
    "core",
    "carry",
    "conditioning",
    "other",
]
PATTERNS: tuple[str, ...] = get_args(Pattern)
LOWER_COMPOUND: frozenset[str] = frozenset({"squat", "hinge", "lunge"})

EquipmentKey = Literal[
    "bodyweight",
    "dumbbells",
    "barbell",
    "squat_rack",
    "bench",
    "cable_machine",
    "smith_machine",
    "leg_press",
    "machines",
    "kettlebells",
    "pull_up_bar",
    "resistance_bands",
    "treadmill",
    "bike",
    "rower",
    "trx",
]
EQUIPMENT_KEYS: tuple[str, ...] = get_args(EquipmentKey)

RATINGS: tuple[str, ...] = ("easy", "ok", "hard")
RATING_LABELS: dict[str, str] = {"easy": "Easy", "ok": "Good", "hard": "Hard"}
UNITS: tuple[str, ...] = ("lb", "kg")
KG_TO_LB = 2.20462

FOCUS_MUSCLES: dict[str, list[MuscleGroup]] = {
    "auto": [],
    "full body": ["chest", "back", "quads", "hamstrings", "shoulders", "core"],
    "upper body": ["chest", "back", "shoulders", "biceps", "triceps"],
    "lower body": ["quads", "hamstrings", "glutes", "calves"],
    "push (chest, shoulders, triceps)": ["chest", "shoulders", "triceps"],
    "pull (back, biceps)": ["back", "biceps"],
    "legs and glutes": ["quads", "glutes", "hamstrings", "calves"],
    "core": ["core"],
    "conditioning / cardio": [],
}

SWAP_REASONS: tuple[tuple[str, str], ...] = (
    ("busy", "Station is busy"),
    ("no_equipment", "Don't have the equipment"),
    ("discomfort", "Feels uncomfortable"),
    ("too_hard", "Too hard today"),
    ("preference", "Just want something else"),
)


def is_canonical(value: str) -> bool:
    return value in CANONICAL


def _canonical_order(items) -> tuple[MuscleGroup, ...]:
    wanted = set(items)
    return tuple(m for m in CANONICAL if m in wanted)


@dataclass(frozen=True)
class MuscleMap:
    primary: MuscleGroup | None  # None = not strength work (cardio, mobility)
    secondary: tuple[MuscleGroup, ...] = ()  # never contains primary, max 4, canonical order
    pattern: Pattern = "other"

    def __post_init__(self):
        primary = self.primary if self.primary in CANONICAL else None
        secondary = _canonical_order(m for m in (self.secondary or ()) if m in CANONICAL and m != primary)
        object.__setattr__(self, "primary", primary)
        object.__setattr__(self, "secondary", secondary[:4])
        if self.pattern not in PATTERNS:
            object.__setattr__(self, "pattern", "other")

    @property
    def all(self) -> tuple[MuscleGroup, ...]:
        """Primary plus secondary, in canonical order."""
        items = list(self.secondary)
        if self.primary:
            items.append(self.primary)
        return _canonical_order(items)


# ---------------------------------------------------------------------------
# Free-text muscle_group -> canonical (AI_COACH 2.2)
# ---------------------------------------------------------------------------
GROUP_SYNONYMS: dict[str, tuple[MuscleGroup, ...]] = {
    # single groups
    "chest": ("chest",),
    "pec": ("chest",),
    "pecs": ("chest",),
    "pectorals": ("chest",),
    "upper chest": ("chest",),
    "lower chest": ("chest",),
    "back": ("back",),
    "lats": ("back",),
    "lat": ("back",),
    "upper back": ("back",),
    "lower back": ("back",),
    "traps": ("back",),
    "trapezius": ("back",),
    "rhomboids": ("back",),
    "erectors": ("back",),
    "spinal erectors": ("back",),
    "mid back": ("back",),
    "shoulders": ("shoulders",),
    "shoulder": ("shoulders",),
    "delts": ("shoulders",),
    "deltoids": ("shoulders",),
    "front delts": ("shoulders",),
    "side delts": ("shoulders",),
    "rear delts": ("shoulders",),
    "rotator cuff": ("shoulders",),
    "biceps": ("biceps",),
    "bicep": ("biceps",),
    "brachialis": ("biceps",),
    "triceps": ("triceps",),
    "tricep": ("triceps",),
    "forearms": ("forearms",),
    "forearm": ("forearms",),
    "grip": ("forearms",),
    "wrists": ("forearms",),
    "core": ("core",),
    "abs": ("core",),
    "abdominals": ("core",),
    "obliques": ("core",),
    "trunk": ("core",),
    "midsection": ("core",),
    "quads": ("quads",),
    "quadriceps": ("quads",),
    "quad": ("quads",),
    "thighs": ("quads",),
    "hamstrings": ("hamstrings",),
    "hamstring": ("hamstrings",),
    "hams": ("hamstrings",),
    "glutes": ("glutes",),
    "glute": ("glutes",),
    "butt": ("glutes",),
    "hips": ("glutes",),
    "hip": ("glutes",),
    "adductors": ("glutes",),
    "abductors": ("glutes",),
    "calves": ("calves",),
    "calf": ("calves",),
    # compound labels (first = primary)
    "legs": ("quads", "glutes", "hamstrings"),
    "lower body": ("quads", "glutes", "hamstrings"),
    "legs and glutes": ("quads", "glutes", "hamstrings"),
    "posterior chain": ("hamstrings", "glutes", "back"),
    "arms": ("biceps", "triceps"),
    "push": ("chest", "shoulders", "triceps"),
    "pull": ("back", "biceps"),
    # no muscle (cardio, mobility, unknown labels)
    "full body": (),
    "upper body": (),
    "cardio": (),
    "conditioning": (),
    "mobility": (),
}
_MAX_PHRASE = max(len(k.split()) for k in GROUP_SYNONYMS)
_GROUP_SEP_RE = re.compile(r"[-_/&+,;()]|\band\b")


def normalize_group(text: str) -> list[MuscleGroup]:
    """``"Chest / Triceps"`` -> ``["chest", "triceps"]``. The first item is the primary."""
    out: list[MuscleGroup] = []
    for segment in _GROUP_SEP_RE.split((text or "").lower()):
        words = segment.split()
        i = 0
        while i < len(words):
            for size in range(min(_MAX_PHRASE, len(words) - i), 0, -1):
                phrase = " ".join(words[i : i + size])
                if phrase in GROUP_SYNONYMS:
                    for m in GROUP_SYNONYMS[phrase]:
                        if m not in out:
                            out.append(m)
                    i += size
                    break
            else:
                i += 1  # unknown word: ignored
    return out


# ---------------------------------------------------------------------------
# Name rules (AI_COACH 2.3)
# ---------------------------------------------------------------------------
_ALIASES = {
    "db": "dumbbell",
    "bb": "barbell",
    "kb": "kettlebell",
    "ohp": "overhead press",
    "rdl": "romanian deadlift",
    "rdls": "romanian deadlift",
}


def normalize_name(name: str) -> str:
    """Lowercase, punctuation/hyphens as spaces, aliases expanded, whitespace collapsed."""
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    return " ".join(_ALIASES.get(w, w) for w in words)


@dataclass(frozen=True)
class NameRule:
    keywords: tuple[str, ...]  # all must appear in the normalized name (plural "s"/"es" allowed)
    primary: MuscleGroup | None
    secondary: tuple[MuscleGroup, ...]
    pattern: Pattern


def _rules(
    alternatives: tuple[tuple[str, ...], ...],
    primary: MuscleGroup | None,
    secondary: tuple[MuscleGroup, ...],
    pattern: Pattern,
) -> list[NameRule]:
    return [NameRule(kw, primary, secondary, pattern) for kw in alternatives]


# Evaluated in order, most specific first. Precedence notes:
#   * conditioning ("rower", "row erg") is checked before "row";
#   * triceps ("close grip" + bench/press, "tricep dip", "bench dip") before bench press and dips;
#   * "wrist curl" before the biceps "curl" rule, leg/hamstring curls before both;
#   * "reverse fly" / "rear delt" (shoulders) before the chest "fly" rule.
NAME_RULES: tuple[NameRule, ...] = tuple(
    _rules(
        (
            ("row erg",),
            ("rower",),
            ("rowing machine",),
            ("treadmill",),
            ("bike",),
            ("jump rope",),
            ("burpee",),
            ("run",),
            ("running",),
        ),
        None,
        (),
        "conditioning",
    )
    + _rules((("romanian deadlift",), ("stiff leg",)), "hamstrings", ("glutes", "back"), "hinge")
    + _rules((("hip thrust",), ("glute bridge",)), "glutes", ("hamstrings",), "hinge")
    + _rules((("deadlift",),), "hamstrings", ("glutes", "back", "forearms"), "hinge")
    + _rules((("good morning",),), "hamstrings", ("back", "glutes"), "hinge")
    + _rules((("front squat",),), "quads", ("glutes", "core"), "squat")
    + _rules(
        (("split squat",), ("bulgarian",), ("lunge",), ("step up",)),
        "quads",
        ("glutes", "hamstrings"),
        "lunge",
    )
    + _rules(
        (
            ("triceps",),
            ("tricep",),
            ("pushdown",),
            ("skull crusher",),
            ("skullcrusher",),
            ("bench dip",),
            ("close grip", "bench"),
            ("close grip", "press"),
            ("overhead", "extension"),
        ),
        "triceps",
        ("chest", "shoulders"),
        "isolation_upper",
    )
    + _rules((("squat",), ("leg press",), ("hack squat",)), "quads", ("glutes", "hamstrings"), "squat")
    + _rules((("leg extension",),), "quads", (), "isolation_lower")
    + _rules((("leg curl",), ("hamstring curl",), ("nordic",)), "hamstrings", ("calves",), "isolation_lower")
    + _rules(
        (("wrist curl",), ("farmer",), ("carry",), ("dead hang",)), "forearms", ("core", "back"), "carry"
    )
    + _rules((("calf raise",), ("calf",)), "calves", (), "isolation_lower")
    + _rules((("abduction",), ("kickback",)), "glutes", (), "isolation_lower")
    + _rules((("pike push up",), ("pike pushup",)), "shoulders", ("triceps",), "vertical_push")
    + _rules((("incline", "press"),), "chest", ("shoulders", "triceps"), "horizontal_push")
    + _rules(
        (("bench press",), ("chest press",), ("floor press",)),
        "chest",
        ("triceps", "shoulders"),
        "horizontal_push",
    )
    + _rules(
        (("push up",), ("pushup",), ("press up",), ("dip",)),
        "chest",
        ("triceps", "shoulders"),
        "horizontal_push",
    )
    + _rules(
        (
            ("lateral raise",),
            ("front raise",),
            ("rear delt",),
            ("reverse fly",),
            ("reverse flye",),
            ("face pull",),
        ),
        "shoulders",
        ("back",),
        "isolation_upper",
    )
    + _rules((("fly",), ("flye",), ("pec deck",), ("crossover",)), "chest", ("shoulders",), "isolation_upper")
    + _rules(
        (
            ("overhead press",),
            ("shoulder press",),
            ("military",),
            ("arnold",),
            ("landmine press",),
        ),
        "shoulders",
        ("triceps",),
        "vertical_push",
    )
    + _rules(
        (("pull up",), ("pullup",), ("chin up",), ("chinup",), ("pulldown",), ("pull down",)),
        "back",
        ("biceps", "forearms"),
        "vertical_pull",
    )
    + _rules((("row",),), "back", ("biceps", "forearms"), "horizontal_pull")
    + _rules((("shrug",),), "back", ("forearms",), "isolation_upper")
    + _rules((("pullover",),), "back", ("chest",), "isolation_upper")
    + _rules(
        (("back extension",), ("hyperextension",), ("superman",)), "back", ("glutes", "hamstrings"), "hinge"
    )
    + _rules((("curl",),), "biceps", ("forearms",), "isolation_upper")
    + _rules(
        (
            ("plank",),
            ("crunch",),
            ("sit up",),
            ("situp",),
            ("leg raise",),
            ("knee raise",),
            ("ab wheel",),
            ("rollout",),
            ("pallof",),
            ("dead bug",),
            ("russian twist",),
            ("hollow",),
        ),
        "core",
        (),
        "core",
    )
)

_KW_CACHE: dict[str, re.Pattern] = {}


def _has(name: str, keyword: str) -> bool:
    pat = _KW_CACHE.get(keyword)
    if pat is None:
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?:e?s)?(?![a-z0-9])")
        _KW_CACHE[keyword] = pat
    return bool(pat.search(name))


def infer_from_name(name: str) -> MuscleMap | None:
    norm = normalize_name(name)
    if not norm:
        return None
    for rule in NAME_RULES:
        if all(_has(norm, kw) for kw in rule.keywords):
            return MuscleMap(rule.primary, rule.secondary, rule.pattern)
    return None


# ---------------------------------------------------------------------------
# Combining label and name (AI_COACH 2.4)
# ---------------------------------------------------------------------------
DEFAULT_SECONDARY: dict[MuscleGroup, tuple[MuscleGroup, ...]] = {
    "chest": ("triceps", "shoulders"),
    "back": ("biceps", "forearms"),
    "shoulders": ("triceps",),
    "quads": ("glutes",),
    "hamstrings": ("glutes",),
    "glutes": ("hamstrings",),
    "biceps": ("forearms",),
    "triceps": (),
    "forearms": (),
    "core": (),
    "calves": (),
}


def map_exercise(name: str, muscle_group: str) -> MuscleMap:
    groups = normalize_group(muscle_group)
    rule = infer_from_name(name)
    if groups:
        primary = groups[0]
        secondary: tuple[MuscleGroup, ...] = tuple(groups[1:])
        if not secondary:
            secondary = rule.secondary if rule and rule.primary == primary else DEFAULT_SECONDARY[primary]
        return MuscleMap(primary, secondary, rule.pattern if rule else "other")
    if rule:
        return rule
    return MuscleMap(None, ())


def _from_row(row: ExerciseMuscles) -> MuscleMap:
    try:
        secondary = json.loads(row.secondary_json or "[]")
    except ValueError:
        secondary = []
    if not isinstance(secondary, list):
        secondary = []
    return MuscleMap(row.primary, tuple(str(s) for s in secondary), row.pattern or "other")


def muscles_for(exercise: Exercise | None, name: str = "") -> MuscleMap:
    """Uses the exercise's ``ExerciseMuscles`` row if any, else the label/name rules."""
    if exercise is not None:
        row = exercise.muscles_row
        if row is not None:
            return _from_row(row)
        return map_exercise(exercise.name, exercise.muscle_group or "")
    return map_exercise(name, "")


def ensure_muscles(exercise: Exercise, *, ai_map: MuscleMap | None = None) -> ExerciseMuscles:
    """Add or refresh the exercise's tag row (no commit). Never overwrites ``source="user"``."""
    from ..extensions import db
    from ..models import ExerciseMuscles

    row = exercise.muscles_row
    if row is not None and row.source == "user":
        return row
    mapped = ai_map if ai_map is not None else map_exercise(exercise.name, exercise.muscle_group or "")
    if row is None:
        row = ExerciseMuscles(user_id=exercise.user_id)
        row.exercise = exercise
        db.session.add(row)
    row.user_id = exercise.user_id
    row.primary = mapped.primary
    row.secondary_json = json.dumps(list(mapped.secondary))
    row.pattern = mapped.pattern
    row.source = "ai" if ai_map is not None else "rules"
    return row


# ---------------------------------------------------------------------------
# Equipment (AI_COACH 2.5)
# ---------------------------------------------------------------------------
_EQUIPMENT_KEYWORDS: tuple[tuple[str, EquipmentKey], ...] = (
    ("leg press", "leg_press"),
    ("smith machine", "smith_machine"),
    ("smith", "smith_machine"),
    ("cable machine", "cable_machine"),
    ("cable", "cable_machine"),
    ("rowing machine", "rower"),
    ("rower", "rower"),
    ("pull up bar", "pull_up_bar"),
    ("pullup bar", "pull_up_bar"),
    ("chin up bar", "pull_up_bar"),
    ("squat rack", "squat_rack"),
    ("power rack", "squat_rack"),
    ("rack", "squat_rack"),
    ("ez bar", "barbell"),
    ("barbell", "barbell"),
    ("dumbbell", "dumbbells"),
    ("kettlebell", "kettlebells"),
    ("bench", "bench"),
    ("machine", "machines"),
    ("band", "resistance_bands"),
    ("trx", "trx"),
    ("suspension", "trx"),
    ("treadmill", "treadmill"),
    ("bike", "bike"),
    ("bodyweight", "bodyweight"),
    ("body weight", "bodyweight"),
    ("none", "bodyweight"),
)


def equipment_keys(text: str) -> frozenset[EquipmentKey]:
    """Free text (or stored keys) -> equipment keys. Empty text means bodyweight."""
    norm = " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip() + " "
    if not norm.strip():
        return frozenset({"bodyweight"})
    found: set[EquipmentKey] = set()
    for keyword, key in _EQUIPMENT_KEYWORDS:
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?:e?s)?(?![a-z0-9])")
        if pat.search(norm):
            found.add(key)
            norm = pat.sub(" ", norm)
    return frozenset(found)


def available(gym_equipment: list[str]) -> frozenset[EquipmentKey]:
    """The gym's equipment keys, always including bodyweight."""
    keys: set[EquipmentKey] = {"bodyweight"}
    for item in gym_equipment or []:
        if item in EQUIPMENT_KEYS:
            keys.add(item)  # type: ignore[arg-type]
        else:
            keys |= equipment_keys(str(item))
    return frozenset(keys)


def library_maps(user_id: int) -> dict[int, MuscleMap]:
    """MuscleMap for every library exercise of one user, in a single query."""
    from ..extensions import db
    from ..models import Exercise, ExerciseMuscles

    rows = (
        db.session.query(Exercise.id, Exercise.name, Exercise.muscle_group, ExerciseMuscles)
        .outerjoin(ExerciseMuscles, ExerciseMuscles.exercise_id == Exercise.id)
        .filter(Exercise.user_id == user_id)
        .all()
    )
    out: dict[int, MuscleMap] = {}
    for ex_id, name, group, tags in rows:
        out[ex_id] = _from_row(tags) if tags is not None else map_exercise(name, group or "")
    return out


def entry_muscles(entry, maps: dict[int, MuscleMap]) -> MuscleMap:
    """Muscles for a SessionEntry: its (owned) library exercise, else its stored name."""
    if entry.exercise_id is not None and entry.exercise_id in maps:
        return maps[entry.exercise_id]
    return map_exercise(entry.exercise_name, "")
