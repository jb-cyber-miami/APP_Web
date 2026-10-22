import re
from datetime import datetime, timedelta

import pytest

from app.services.progression import Exposure, PerformedSet
from app.services.strength import (
    compute_strength,
    index_pct,
    sparkline_points,
    strength_report,
    strength_rows,
)

NOW = datetime(2026, 10, 21, 12, 0, 0)


def _exp(sid, days_ago, weight_lb, reps, n=3):
    s = PerformedSet(
        reps=reps, seconds=None, weight_lb=weight_lb, rating=None, target_reps=None, target_seconds=None
    )
    return Exposure(session_id=sid, performed_at=NOW - timedelta(days=days_ago), sets=(s,) * n)


def _e1rm_to_weight(e1rm, reps=5):
    return e1rm / (1 + reps / 30)


def _lift(baseline, current, sid_base):
    # newest first, like progression.load_all_history
    return [
        _exp(sid_base + 1, 3, _e1rm_to_weight(current), 5),
        _exp(sid_base, 60, _e1rm_to_weight(baseline), 5),
    ]


def _example():
    exposures = {
        "id:1": _lift(200, 234, 10),
        "id:2": _lift(250, 275, 20),
        "id:3": _lift(160, 168, 30),
    }
    names = {"id:1": "Bench Press", "id:2": "Back Squat", "id:3": "Barbell Row"}
    muscles = {"id:1": "chest", "id:2": "quads", "id:3": "back"}
    return exposures, names, muscles


def test_worked_example_overall_111():
    report = compute_strength(*_example(), as_of=NOW, exercise_ids={"id:1": 1, "id:2": 2, "id:3": 3})
    assert {m: s.index for m, s in report.muscles.items()} == {"chest": 117, "back": 105, "quads": 110}
    assert report.overall == 111
    bench = next(lf for lf in report.lifts if lf.key == "id:1")
    assert (bench.index, bench.stale, bench.pr, bench.exercise_id) == (117, False, True, 1)
    assert bench.current_e1rm == 234.0
    assert re.fullmatch(r"\d+(\.\d)? lb × 5", bench.best_set)
    # sorted by muscle (canonical), then -current e1RM
    assert [lf.muscle for lf in report.lifts] == ["chest", "back", "quads"]


def test_overall_none_with_fewer_than_three_groups():
    exposures, names, muscles = _example()
    del exposures["id:3"]
    report = compute_strength(exposures, names, muscles, as_of=NOW)
    assert report.overall is None and len(report.muscles) == 2


def test_empty_history():
    report = compute_strength({}, {}, {}, as_of=NOW)
    assert (report.overall, report.overall_delta_28d, report.muscles, report.lifts) == (None, None, {}, [])


def test_bodyweight_and_high_rep_sets_are_not_lifts():
    exposures = {
        "name:push up": [_exp(1, 2, None, 15)],
        "name:leg press": [_exp(2, 2, 300, 20)],  # > 12 reps: no e1RM
    }
    report = compute_strength(exposures, {}, {"name:push up": "chest", "name:leg press": "quads"}, as_of=NOW)
    assert report.lifts == []


def test_stale_lift_uses_last_value_and_old_lifts_excluded():
    exposures = {
        "id:1": [_exp(2, 40, 200, 5), _exp(1, 70, 180, 5)],  # nothing in 28 days -> stale
        "id:2": [_exp(3, 100, 200, 5)],  # nothing in 90 days -> excluded
    }
    report = compute_strength(exposures, {}, {"id:1": "chest", "id:2": "back"}, as_of=NOW)
    assert [lf.key for lf in report.lifts] == ["id:1"]
    lift = report.lifts[0]
    assert lift.stale and lift.index == 111 and lift.pr


def test_baseline_window_is_first_14_days():
    exposures = {"id:1": [_exp(3, 5, 230, 5), _exp(2, 50, 220, 5), _exp(1, 60, 200, 5)]}
    lift = compute_strength(exposures, {}, {"id:1": "chest"}, as_of=NOW).lifts[0]
    # baseline = max(day 60, day 50) since both are within 14 days of the first exposure
    assert lift.index == round(100 * 230 / 220)


def test_no_pr_when_not_beating_earlier():
    exposures = {"id:1": [_exp(2, 2, 180, 5), _exp(1, 9, 200, 5)]}
    assert compute_strength(exposures, {}, {"id:1": "chest"}, as_of=NOW).lifts[0].pr is False


def test_delta_28d():
    exposures, names, muscles = _example()
    # add exposures 40 days ago so a value exists as of now - 28 days
    exposures = {
        k: [v[0], _exp(v[0].session_id + 5, 40, v[1].sets[0].weight_lb * 1.05, 5), v[1]]
        for k, v in exposures.items()
    }
    report = compute_strength(exposures, names, muscles, as_of=NOW)
    assert report.muscles["chest"].delta_28d == 117 - 105
    assert report.overall_delta_28d == report.overall - 105


def test_delta_none_without_earlier_data():
    report = compute_strength(
        {"id:1": [_exp(1, 3, 200, 5), _exp(2, 5, 190, 5)]}, {}, {"id:1": "chest"}, as_of=NOW
    )
    assert report.muscles["chest"].delta_28d is None
    assert report.overall_delta_28d is None


def test_kg_display():
    report = compute_strength({"id:1": [_exp(1, 1, 220.462, 1)]}, {}, {"id:1": "chest"}, as_of=NOW, unit="kg")
    lift = report.lifts[0]
    assert lift.current_e1rm == 100.0
    assert lift.best_set == "100 kg × 1"


@pytest.mark.parametrize(
    "index,pct", [(None, 0), (0, 0), (100, 67), (111, 74), (150, 100), (240, 100), (-5, 0)]
)
def test_index_pct(index, pct):
    assert index_pct(index) == pct


def test_strength_rows_cover_all_groups():
    report = compute_strength(*_example(), as_of=NOW)
    rows = strength_rows(report)
    assert len(rows) == 11
    chest = rows[0]
    assert (chest.muscle, chest.label, chest.index, chest.pct, chest.lifts) == ("chest", "Chest", 117, 78, 1)
    assert rows[-1].index is None and rows[-1].pct == 0


def test_sparkline_points():
    assert sparkline_points([]) is None
    assert sparkline_points([5]) is None
    pts = sparkline_points([100, 110, 105, float("nan"), 120])
    assert re.fullmatch(r"(\d+\.\d,\d+\.\d ?)+", pts)
    coords = [tuple(map(float, p.split(","))) for p in pts.split()]
    assert coords[0][0] == 2.0 and coords[-1][0] == 98.0
    assert coords[-1][1] == 2.0 and coords[0][1] == 30.0  # highest value at the top
    flat = sparkline_points([7, 7, 7])
    assert all(p.endswith(",16.0") for p in flat.split())
    assert sparkline_points(["<script>", 1, 2]) == "2.0,30.0 98.0,2.0"


def test_strength_report_from_db(app, alice):
    from app.extensions import db
    from app.models import Exercise, SessionEntry, User, WorkoutSession

    uid = User.query.one().id
    for i, (name, group, weights) in enumerate(
        [
            ("Bench Press", "Chest", (185, 200)),
            ("Back Squat", "Quads", (225, 245)),
            ("Barbell Row", "Back", (135, 145)),
        ]
    ):
        ex = Exercise(user_id=uid, name=name, muscle_group=group)
        db.session.add(ex)
        db.session.flush()
        for days_ago, w in ((40, weights[0]), (3, weights[1])):
            start = NOW - timedelta(days=days_ago, hours=i)
            ws = WorkoutSession(
                user_id=uid, day=start.date(), started_at=start, ended_at=start + timedelta(hours=1)
            )
            db.session.add(ws)
            db.session.flush()
            db.session.add(
                SessionEntry(
                    session_id=ws.id,
                    exercise_id=ex.id,
                    exercise_name=name,
                    sets_done=3,
                    reps_done="5",
                    weight=f"{w} lb",
                )
            )
    db.session.commit()
    report = strength_report(uid, now=NOW)
    assert set(report.muscles) == {"chest", "quads", "back"}
    assert report.muscles["chest"].index == round(100 * 200 / 185)
    assert report.overall is not None
