"""Progressive overload (docs/AI_COACH.md section 4): parsing, e1RM, rounding, next targets, history."""

from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import EntryPlan, Exercise, SessionEntry, SetLog, User, WorkoutSession
from app.services import progression as p

NOW = datetime(2026, 10, 21, 12, 0)
BARBELL = frozenset({"barbell"})


def _exp(reps, weight, rating=None, *, target=None, days=2, sid=1, seconds=None):
    sets = []
    for i, r in enumerate(reps):
        sets.append(
            p.PerformedSet(
                reps=r,
                seconds=seconds[i] if seconds else None,
                weight_lb=weight,
                rating=rating,
                target_reps=target,
                target_seconds=None,
            )
        )
    return p.Exposure(sid, NOW - timedelta(days=days), tuple(sets))


def _rx(low, high, *, sets=3, pattern="other", equipment=BARBELL):
    return p.Prescription(sets=sets, rep_range=(low, high), pattern=pattern, equipment=equipment)


def _plan(history, rx, unit="lb"):
    result = p.next_targets(history, rx, unit=unit, now=NOW)
    return result, [(t.reps, t.weight) for t in result.targets]


# ---------------------------------------------------------------------------
# 4.1 parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "value", "unit", "bw"),
    [
        ("135", 135.0, "lb", False),
        ("60kg", 60.0, "kg", False),
        ("60 kg", 60.0, "kg", False),
        ("25 lb", 25.0, "lb", False),
        ("25lbs", 25.0, "lb", False),
        ("62,5 kg", 62.5, "kg", False),
        ("bw", None, "lb", True),
        ("Bodyweight", None, "lb", True),
        ("", None, "lb", True),
        ("2x20", None, "lb", False),
        ("heavy", None, "lb", False),
        ("9999", 2000.0, "lb", False),
    ],
)
def test_parse_weight(text, value, unit, bw):
    assert p.parse_weight(text) == p.ParsedWeight(value, unit, bw)


def test_parse_weight_default_unit():
    assert p.parse_weight("100", "kg") == p.ParsedWeight(100.0, "kg", False)
    assert p.parse_weight("100 lb", "kg").unit == "lb"


@pytest.mark.parametrize(
    ("text", "reps"),
    [("10", 10), ("8-10", 8), ("8–12", 8), ("12 reps", 12), ("30s", None), ("AMRAP", None), ("", None)],
)
def test_parse_reps(text, reps):
    assert p.parse_reps(text) == reps


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("30s", 30), ("45 sec", 45), ("1 min", 60), ("1:30", 90), ("30-45s", 30), ("10", None), ("", None)],
)
def test_parse_seconds(text, seconds):
    assert p.parse_seconds(text) == seconds


@pytest.mark.parametrize(
    ("text", "rng"), [("8-12", (8, 12)), ("12-8", (8, 12)), ("10", (10, 10)), ("30s", None), ("x", None)]
)
def test_parse_rep_range(text, rng):
    assert p.parse_rep_range(text) == rng


def test_units_and_format():
    assert p.to_lb(100, "kg") == pytest.approx(220.462)
    assert p.from_lb(220.462, "kg") == pytest.approx(100)
    assert p.to_lb(135, "lb") == 135
    assert p.fmt_weight(135.0, "lb") == "135 lb"
    assert p.fmt_weight(62.5, "kg") == "62.5 kg"
    assert p.fmt_weight(None, "lb") == ""
    assert p.KG_TO_LB == 2.20462


# ---------------------------------------------------------------------------
# 4.3 / 4.4 e1RM and rounding
# ---------------------------------------------------------------------------
def test_epley_bounds():
    assert p.epley(185, 8) == pytest.approx(234.333, abs=0.01)
    assert p.epley(200, 1) == 200
    assert p.epley(100, 13) is None
    assert p.epley(100, 0) is None
    assert p.epley(0, 5) is None


def test_weight_for_reps_example():
    e1rm = p.epley(185, 8)
    assert p.weight_for_reps(e1rm, 5) == pytest.approx(190, abs=0.01)
    ten = p.weight_for_reps(e1rm, 10)
    assert ten == pytest.approx(167.4, abs=0.05)
    assert p.round_load(ten, 5, "down") == 165


def test_exposure_e1rm_ignores_bodyweight():
    exp = p.Exposure(1, NOW, (p.PerformedSet(10, None, None, None, None, None),))
    assert p.exposure_e1rm(exp) is None
    assert p.exposure_e1rm(_exp([8, 5], 185)) == pytest.approx(234.333, abs=0.01)


def test_increment_priority_and_rounding():
    assert p.increment(frozenset({"leg_press", "barbell"}), "lb") == 10
    assert p.increment(frozenset({"barbell", "dumbbells"}), "lb") == 5
    assert p.increment(frozenset({"dumbbells"}), "kg") == 2
    assert p.increment(frozenset({"kettlebells", "machines"}), "kg") == 4
    assert p.increment(frozenset(), "kg") == 2.5
    assert p.round_load(236.25, 5) == 235
    assert p.round_load(189.6, 5) == 190
    assert p.round_load(166.5, 5, "down") == 165
    assert p.round_load(61.3, 2.5) == 62.5
    assert p.round_load(61.3, 2.5, "down") == 60


# ---------------------------------------------------------------------------
# 4.5 next targets: the worked examples
# ---------------------------------------------------------------------------
def test_no_history():
    result, rows = _plan([], _rx(8, 12))
    assert result.rule == "no_history" and result.source == "none"
    assert rows == [(8, None)] * 3


def test_squat_easy_lower_compound():
    result, rows = _plan([_exp([5, 5, 5], 225, "easy")], _rx(5, 5, pattern="squat"))
    assert result.rule == "easy_up"
    assert rows == [(5, 235)] * 3


def test_bench_easy_upper():
    result, rows = _plan([_exp([8, 8, 8], 185, "easy")], _rx(8, 12))
    assert result.rule == "easy_up"
    assert rows == [(8, 190)] * 3
    assert "+5 lb" in result.reason


def test_bench_ok_at_top_adds_weight():
    result, rows = _plan([_exp([12, 12, 12], 185)], _rx(8, 12))
    assert result.rule == "add_weight"
    assert rows == [(8, 190)] * 3


def test_bench_ok_not_at_top_adds_reps():
    result, rows = _plan([_exp([12, 11, 10], 185)], _rx(8, 12))
    assert result.rule == "add_reps"
    assert rows == [(12, 185), (12, 185), (11, 185)]


def test_hard_twice_drops():
    history = [_exp([8, 8, 8], 185, "hard"), _exp([8, 8, 8], 185, "hard", days=5, sid=2)]
    result, rows = _plan(history, _rx(8, 12))
    assert result.rule == "hard_drop"
    assert rows == [(8, 175)] * 3


def test_hard_once_holds():
    result, rows = _plan([_exp([9, 8, 8], 185, "hard")], _rx(8, 12))
    assert result.rule == "hold"
    assert rows == [(9, 185), (8, 185), (8, 185)]


def test_missed_twice_deloads():
    history = [_exp([8, 7, 6], 185, target=8), _exp([8, 7, 6], 185, target=8, days=5, sid=2)]
    result, rows = _plan(history, _rx(8, 12))
    assert result.rule == "deload"
    assert rows == [(8, 165)] * 3
    assert "165 lb" in result.reason


def test_missed_once_retries():
    result, rows = _plan([_exp([8, 7, 6], 185, target=8)], _rx(8, 12))
    assert result.rule == "retry"
    assert rows == [(8, 185)] * 3


def test_rerange_uses_e1rm():
    result, rows = _plan([_exp([8, 8, 8], 185)], _rx(5, 5, sets=4))
    assert result.rule == "rerange" and result.source == "e1rm"
    assert rows == [(5, 190)] * 4


def test_gap_beats_deload():
    history = [_exp([8, 7, 6], 185, target=8, days=30), _exp([8, 7, 6], 185, target=8, days=35, sid=2)]
    result, rows = _plan(history, _rx(8, 12))
    assert result.rule == "gap"
    assert rows == [(8, 165)] * 3


def test_change_is_at_least_one_increment():
    # 1.025 x 20 = 20.5 rounds back to 20; an increase must still add one increment.
    result, rows = _plan([_exp([8, 8, 8], 20, "easy")], _rx(8, 12, equipment=frozenset({"dumbbells"})))
    assert rows[0] == (8, 25)


def test_kg_rounding_in_display_unit():
    history = [_exp([5, 5, 5], p.to_lb(100, "kg"), "easy")]
    result, rows = _plan(history, _rx(5, 5, pattern="squat"), unit="kg")
    assert rows == [(5, 105.0)] * 3
    assert result.targets[0].unit == "kg"


def test_extra_sets_reuse_last_target():
    result, rows = _plan([_exp([12, 11], 185)], _rx(8, 12, sets=4))
    assert rows == [(12, 185), (12, 185), (12, 185), (12, 185)]


def test_bodyweight_progression():
    easy = _plan([_exp([10, 10, 10], None, "easy")], _rx(8, 12))
    assert easy[0].rule == "easy_up" and easy[1] == [(12, None)] * 3
    ok = _plan([_exp([10, 10, 10], None)], _rx(8, 12))
    assert ok[0].rule == "add_reps" and ok[1] == [(11, None)] * 3
    hard = _plan([_exp([10, 10, 10], None, "hard")], _rx(8, 12))
    assert hard[1] == [(10, None)] * 3
    ready = _plan([_exp([18, 18, 18], None)], _rx(8, 12))
    assert "harder variation" in ready[0].reason


def test_timed_progression():
    rx = p.Prescription(sets=3, rep_range=None, seconds=30, pattern="core")

    def timed(rating):
        exp = p.Exposure(
            1,
            NOW - timedelta(days=2),
            tuple(p.PerformedSet(None, 30, None, rating, None, 30) for _ in range(3)),
        )
        return p.next_targets([exp], rx, unit="lb", now=NOW)

    assert [t.seconds for t in timed("easy").targets] == [40, 40, 40]
    assert [t.seconds for t in timed(None).targets] == [35, 35, 35]
    assert [t.seconds for t in timed("hard").targets] == [30, 30, 30]
    assert all(t.reps is None for t in timed(None).targets)
    first = p.next_targets([], rx, unit="lb", now=NOW)
    assert [t.seconds for t in first.targets] == [30, 30, 30]


def test_to_planned_sets():
    result, _ = _plan([_exp([8, 8, 8], 185, "easy")], _rx(8, 12))
    planned = p.to_planned_sets(result)
    assert [(s.target_reps, s.weight, s.weight_source) for s in planned] == [(8, 190, "history")] * 3
    none = p.to_planned_sets(p.next_targets([], _rx(8, 12), unit="kg", now=NOW))
    assert none[0].weight is None and none[0].weight_source == "none" and none[0].unit == "kg"


def test_exercise_key():
    assert p.exercise_key(12, "x") == "id:12"
    assert p.exercise_key(None, "DB Bench-Press") == "name:dumbbell bench press"


# ---------------------------------------------------------------------------
# History loaders (DB)
# ---------------------------------------------------------------------------
def _user():
    return User.query.filter_by(username="alice").one()


def _session(user, *, days_ago, ended=True):
    started = NOW - timedelta(days=days_ago)
    ws = WorkoutSession(
        user_id=user.id,
        day=started.date(),
        started_at=started,
        ended_at=started + timedelta(hours=1) if ended else None,
    )
    db.session.add(ws)
    db.session.flush()
    return ws


def test_load_history_legacy_tracked_and_open_session(alice):
    user = _user()
    bench = Exercise(user_id=user.id, name="Bench Press", muscle_group="Chest")
    db.session.add(bench)
    db.session.flush()

    old = _session(user, days_ago=6)  # legacy entry, expands by sets_done
    db.session.add(
        SessionEntry(
            session_id=old.id,
            exercise_id=bench.id,
            exercise_name="Bench Press",
            sets_done=3,
            reps_done="8",
            weight="135",
        )
    )
    new = _session(user, days_ago=2)  # tracked entry with two logged sets
    entry = SessionEntry(session_id=new.id, exercise_id=bench.id, exercise_name="Bench Press")
    db.session.add(entry)
    db.session.flush()
    db.session.add(EntryPlan(user_id=user.id, entry_id=entry.id, targets_json="[]"))
    for i, reps in enumerate((10, 9)):
        db.session.add(
            SetLog(
                user_id=user.id,
                entry_id=entry.id,
                set_index=i,
                reps=reps,
                weight=60,
                unit="kg",
                rating="hard" if i else None,
                target_reps=10,
                logged_at=new.started_at + timedelta(minutes=i),
            )
        )
    tracked_empty = _session(user, days_ago=1)  # tracked entry with no logs contributes nothing
    e2 = SessionEntry(session_id=tracked_empty.id, exercise_id=bench.id, exercise_name="Bench Press")
    db.session.add(e2)
    db.session.flush()
    db.session.add(EntryPlan(user_id=user.id, entry_id=e2.id, targets_json="[]"))
    current = WorkoutSession(user_id=user.id, day=date(2026, 10, 21), started_at=NOW - timedelta(minutes=30))
    db.session.add(current)  # in progress: skipped
    db.session.flush()
    db.session.add(
        SessionEntry(
            session_id=current.id,
            exercise_id=bench.id,
            exercise_name="Bench Press",
            sets_done=5,
            reps_done="5",
            weight="200",
        )
    )
    db.session.commit()

    history = p.load_history(user.id, f"id:{bench.id}", now=NOW)
    assert [e.session_id for e in history] == [new.id, old.id]
    assert [(s.reps, round(s.weight_lb, 1), s.rating) for s in history[0].sets] == [
        (10, 132.3, None),
        (9, 132.3, "hard"),
    ]
    assert len(history[1].sets) == 3 and history[1].sets[0].weight_lb == 135

    # The exercise is deleted: entries keep the name, and a name key still finds them.
    SessionEntry.query.filter_by(exercise_id=bench.id).update({"exercise_id": None})
    db.session.commit()
    by_name = p.load_history(user.id, p.exercise_key(None, "bench press"), now=NOW)
    assert [e.session_id for e in by_name] == [new.id, old.id]

    grouped, names, ids = p.load_all_history(user.id, now=NOW)
    assert names == {"name:bench press": "Bench Press"}
    assert ids == {"name:bench press": None}
    assert len(grouped["name:bench press"]) == 2


def test_load_history_id_key_matches_deleted_entries_by_name(alice):
    user = _user()
    old_ws = _session(user, days_ago=5)
    db.session.add(
        SessionEntry(
            session_id=old_ws.id,
            exercise_id=None,
            exercise_name="Goblet  Squat",
            sets_done=2,
            reps_done="10",
            weight="bw",
        )
    )
    goblet = Exercise(user_id=user.id, name="Goblet squat")
    db.session.add(goblet)
    db.session.commit()
    history = p.load_history(user.id, f"id:{goblet.id}", now=NOW)
    assert len(history) == 1 and history[0].sets[0].weight_lb is None and history[0].sets[0].reps == 10
    assert p.load_history(user.id, "id:99999", now=NOW) == []
