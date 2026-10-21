from typing import get_args

import pytest

from app import forms
from app.services import muscles
from app.services.muscles import (
    CANONICAL,
    FOCUS_MUSCLES,
    MuscleMap,
    available,
    equipment_keys,
    infer_from_name,
    map_exercise,
    normalize_group,
    normalize_name,
)


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Chest / Triceps", ["chest", "triceps"]),
        ("legs", ["quads", "glutes", "hamstrings"]),
        ("Lats", ["back"]),
        ("", []),
        ("Upper chest, front delts", ["chest", "shoulders"]),
        ("full body", []),
        ("legs and glutes", ["quads", "glutes", "hamstrings"]),
        ("Rear Delts & Traps", ["shoulders", "back"]),
        ("mystery muscle", []),
    ],
)
def test_normalize_group(label, expected):
    assert normalize_group(label) == expected


@pytest.mark.parametrize(
    "name,primary,pattern",
    [
        ("Close-Grip Bench", "triceps", "isolation_upper"),
        ("Close grip bench press", "triceps", "isolation_upper"),
        ("Rower", None, "conditioning"),
        ("Row erg intervals", None, "conditioning"),
        ("Barbell Row", "back", "horizontal_pull"),
        ("DB RDL", "hamstrings", "hinge"),
        ("Romanian Deadlift", "hamstrings", "hinge"),
        ("Deadlift", "hamstrings", "hinge"),
        ("Hip Thrust", "glutes", "hinge"),
        ("Front Squat", "quads", "squat"),
        ("Bulgarian split squat", "quads", "lunge"),
        ("Goblet Squat", "quads", "squat"),
        ("Leg Press", "quads", "squat"),
        ("Lying Leg Curl", "hamstrings", "isolation_lower"),
        ("Barbell Curl", "biceps", "isolation_upper"),
        ("Wrist curl", "forearms", "carry"),
        ("Incline Dumbbell Press", "chest", "horizontal_push"),
        ("Push-ups", "chest", "horizontal_push"),
        ("Reverse fly", "shoulders", "isolation_upper"),
        ("Cable Fly", "chest", "isolation_upper"),
        ("OHP", "shoulders", "vertical_push"),
        ("Lat Pulldown", "back", "vertical_pull"),
        ("Close grip lat pulldown", "back", "vertical_pull"),
        ("Triceps Pushdown", "triceps", "isolation_upper"),
        ("Plank", "core", "core"),
        ("Hanging leg raise", "core", "core"),
        ("Standing calf raise", "calves", "isolation_lower"),
        ("Farmer carry", "forearms", "carry"),
    ],
)
def test_name_rules_and_precedence(name, primary, pattern):
    mm = infer_from_name(name)
    assert mm is not None
    assert (mm.primary, mm.pattern) == (primary, pattern)


def test_unknown_name_is_none():
    assert infer_from_name("Kettlebell windmill dance") is None
    assert infer_from_name("") is None


def test_normalize_name_aliases():
    assert normalize_name("  DB   Bench-Press ") == "dumbbell bench press"
    assert normalize_name("OHP") == "overhead press"


def test_map_exercise_combines_label_and_name():
    # label wins for primary; rule secondaries used when the rule agrees on the primary
    assert map_exercise("Bench Press", "Chest") == MuscleMap(
        "chest", ("triceps", "shoulders"), "horizontal_push"
    )
    # label disagrees with the rule -> DEFAULT_SECONDARY for the label's primary
    assert map_exercise("Bench Press", "Shoulders") == MuscleMap("shoulders", ("triceps",), "horizontal_push")
    # explicit secondaries from the label
    assert map_exercise("Mystery", "Back / Biceps").secondary == ("biceps",)
    # no label -> name rule
    assert map_exercise("Romanian Deadlift", "").primary == "hamstrings"
    # nothing known -> counts toward nothing
    assert map_exercise("Mystery", "") == MuscleMap(None, ())


def test_muscle_map_invariants():
    mm = MuscleMap("chest", ("chest", "triceps", "shoulders", "bogus", "triceps"), "nope")
    assert mm.secondary == ("shoulders", "triceps")  # no primary, no dupes, canonical order
    assert mm.pattern == "other"
    assert mm.all == ("chest", "shoulders", "triceps")
    assert MuscleMap("calves", ("chest", "back", "core", "quads", "glutes")).secondary == (
        "chest",
        "back",
        "core",
        "quads",
    )


def test_equipment_keys_match_forms():
    assert list(get_args(muscles.EquipmentKey)) == [k for k, _ in forms.EQUIPMENT]


def test_focus_muscles_cover_forms_focus():
    assert set(FOCUS_MUSCLES) >= {k for k, _ in forms.FOCUS} | {"auto"}
    assert FOCUS_MUSCLES["auto"] == []
    for groups in FOCUS_MUSCLES.values():
        assert all(g in CANONICAL for g in groups)


def test_swap_reasons_and_rating_labels():
    assert [k for k, _ in muscles.SWAP_REASONS] == [
        "busy",
        "no_equipment",
        "discomfort",
        "too_hard",
        "preference",
    ]
    assert muscles.RATING_LABELS["ok"] == "Good"
    assert muscles.RATINGS == ("easy", "ok", "hard")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", {"bodyweight"}),
        ("none", {"bodyweight"}),
        ("Dumbbells", {"dumbbells"}),
        ("barbell, bench", {"barbell", "bench"}),
        ("Cable machine", {"cable_machine"}),
        ("smith machine", {"smith_machine"}),
        ("pull-up bar", {"pull_up_bar"}),
        ("EZ bar", {"barbell"}),
        ("bands", {"resistance_bands"}),
        ("leg press machine", {"leg_press", "machines"}),
    ],
)
def test_equipment_keys(text, expected):
    assert equipment_keys(text) == frozenset(expected)


def test_available_always_has_bodyweight():
    assert available([]) == frozenset({"bodyweight"})
    assert available(["dumbbells", "bench"]) == frozenset({"bodyweight", "dumbbells", "bench"})


def test_ensure_muscles_rules_ai_and_user(app, alice):
    from app.extensions import db
    from app.models import Exercise, ExerciseMuscles, User
    from app.services.muscles import ensure_muscles, muscles_for

    uid = User.query.one().id
    ex = Exercise(user_id=uid, name="Bench Press", muscle_group="Chest")
    db.session.add(ex)
    row = ensure_muscles(ex)
    db.session.commit()
    assert (row.source, row.primary, row.exercise_id) == ("rules", "chest", ex.id)
    assert muscles_for(ex).secondary == ("shoulders", "triceps")

    ensure_muscles(ex, ai_map=MuscleMap("triceps", ("chest",), "horizontal_push"))
    db.session.commit()
    assert ExerciseMuscles.query.one().source == "ai"
    assert muscles_for(ex).primary == "triceps"

    row.source = "user"
    row.primary = "shoulders"
    db.session.commit()
    ensure_muscles(ex)  # never overwrites a user row
    db.session.commit()
    assert ExerciseMuscles.query.one().primary == "shoulders"


def test_manual_exercise_gets_muscle_tags(alice):
    from app.models import ExerciseMuscles

    alice.post("/exercises/new", data={"name": "Goblet Squat", "sets": 3, "reps": "10", "rest_seconds": 60})
    row = ExerciseMuscles.query.one()
    assert (row.primary, row.source) == ("quads", "rules")
