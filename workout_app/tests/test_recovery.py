from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import EntryPlan, Exercise, SessionEntry, User, WorkoutSession
from app.services import recovery
from app.services.recovery import (
    MuscleRecovery,
    WorkSet,
    compute_recovery,
    neglected_muscles,
    rec_class,
    recovery_map,
    recovery_panel,
)

NOW = datetime(2026, 10, 21, 12, 0, 0)


def _sets(n, *, hours_ago, primary, secondary=(), session_id=1, rating=None):
    at = NOW - timedelta(hours=hours_ago)
    return [WorkSet(session_id, at, primary, tuple(secondary), rating) for _ in range(n)]


def _chest_day(hours_ago=24):
    # 4 bench (chest; triceps + shoulders secondary) + 3 fly (chest; shoulders) = 7 effective chest sets
    return _sets(4, hours_ago=hours_ago, primary="chest", secondary=("triceps", "shoulders")) + _sets(
        3, hours_ago=hours_ago, primary="chest", secondary=("shoulders",)
    )


def test_fixture_chest_seven_sets_24h():
    rmap = compute_recovery(_chest_day(24), NOW)
    chest = rmap["chest"]
    assert (chest.recovery, chest.status) == (49, "fatigued")
    assert chest.sets_7d == 7.0
    assert chest.hours_since == 24.0
    assert chest.ready_at == NOW + timedelta(hours=24)


@pytest.mark.parametrize("hours,expected", [(36, 67), (48, 85), (60, 100)])
def test_fixture_chest_over_time(hours, expected):
    assert compute_recovery(_chest_day(hours), NOW)["chest"].recovery == expected


def test_fixture_triceps_secondary():
    tri = compute_recovery(_sets(4, hours_ago=24, primary="chest", secondary=("triceps",)), NOW)["triceps"]
    assert (tri.recovery, tri.status) == (96, "fresh")
    assert tri.ready_at is None


def test_fixture_quads_ten_sets_48h():
    quads = compute_recovery(_sets(10, hours_ago=48, primary="quads"), NOW)["quads"]
    assert (quads.recovery, quads.status) == (67, "recovering")


def test_fixture_two_chest_sessions():
    sets = _sets(6, hours_ago=72, primary="chest", session_id=1) + _sets(
        4, hours_ago=24, primary="chest", session_id=2
    )
    assert compute_recovery(sets, NOW)["chest"].recovery == 78


def test_nothing_logged():
    rmap = compute_recovery([], NOW)
    assert list(rmap) == list(recovery.CANONICAL)
    for m in rmap.values():
        assert (m.recovery, m.status, m.last_trained_at, m.ready_at) == (100, "fresh", None, None)


def test_rating_multiplier():
    hard = compute_recovery(_sets(4, hours_ago=12, primary="chest", rating="hard"), NOW)["chest"]
    easy = compute_recovery(_sets(4, hours_ago=12, primary="chest", rating="easy"), NOW)["chest"]
    assert hard.sets_7d == 5.0 and easy.sets_7d == 3.2
    assert hard.recovery < easy.recovery


def test_old_sets_outside_lookback_ignored():
    assert compute_recovery(_sets(10, hours_ago=24 * 8, primary="back"), NOW)["back"].last_trained_at is None


def _mr(value, trained=True):
    return MuscleRecovery("chest", value, recovery._status(value), NOW if trained else None, 1.0, 1.0, None)


@pytest.mark.parametrize(
    "value,cls,status",
    [
        (100, "rec-4", "fresh"),
        (85, "rec-4", "fresh"),
        (84, "rec-3", "recovering"),
        (70, "rec-3", "recovering"),
        (69, "rec-2", "recovering"),
        (50, "rec-2", "recovering"),
        (49, "rec-1", "fatigued"),
        (25, "rec-1", "fatigued"),
        (24, "rec-0", "fatigued"),
        (0, "rec-0", "fatigued"),
    ],
)
def test_rec_class_boundaries_and_status(value, cls, status):
    assert rec_class(_mr(value)) == cls
    assert recovery._status(value) == status


def test_never_trained_is_rec_na():
    assert rec_class(_mr(100, trained=False)) == "rec-na"


def test_recovery_panel():
    panel = recovery_panel(compute_recovery(_chest_day(24), NOW), NOW)
    assert len(panel.items) == 11 and panel.has_history
    chest = panel.by_muscle["chest"]
    assert (chest.pct, chest.status_label, chest.rec, chest.ready_in_h) == (49, "Fatigued", "rec-1", 24)
    assert panel.by_muscle["back"].rec == "rec-na" and not panel.by_muscle["back"].trained
    assert list(panel.groups) == ["fresh", "recovering", "fatigued"]
    assert [v.muscle for v in panel.groups["fatigued"]] == ["chest"]
    assert panel.desc.startswith("Chest 49% recovered, fatigued. Back 100% recovered, fresh.")
    assert not recovery_panel(compute_recovery([], NOW), NOW).has_history


# ---------------------------------------------------------------------------
# Database loader
# ---------------------------------------------------------------------------
def _session(uid, *, hours_ago, ended=True):
    start = NOW - timedelta(hours=hours_ago + 1)
    ws = WorkoutSession(
        user_id=uid,
        day=start.date(),
        started_at=start,
        ended_at=start + timedelta(hours=1) if ended else None,
    )
    db.session.add(ws)
    db.session.flush()
    return ws


def test_legacy_entries_expand_by_sets_done(app, alice):
    uid = User.query.one().id
    ex = Exercise(user_id=uid, name="Bench Press", muscle_group="Chest")
    db.session.add(ex)
    ws = _session(uid, hours_ago=24)
    db.session.add(SessionEntry(session_id=ws.id, exercise_id=ex.id, exercise_name=ex.name, sets_done=4))
    db.session.add(SessionEntry(session_id=ws.id, exercise_name="Cable Fly", sets_done=3))
    db.session.commit()
    sets = recovery.load_work_sets(uid, NOW - recovery.LOOKBACK)
    assert len(sets) == 7
    assert recovery_map(uid, NOW)["chest"].recovery == 49


def test_tracked_entry_without_logs_contributes_nothing(app, alice):
    uid = User.query.one().id
    ws = _session(uid, hours_ago=24)
    entry = SessionEntry(session_id=ws.id, exercise_name="Back Squat", sets_done=5)
    db.session.add(entry)
    db.session.add(EntryPlan(user_id=uid, entry=entry, targets_json='[{"target_reps": 5}]'))
    db.session.commit()
    assert recovery.load_work_sets(uid, NOW - recovery.LOOKBACK) == []
    assert recovery_map(uid, NOW)["quads"].recovery == 100


def test_cardio_entries_skipped(app, alice):
    uid = User.query.one().id
    ws = _session(uid, hours_ago=5)
    db.session.add(SessionEntry(session_id=ws.id, exercise_name="Treadmill run", sets_done=1))
    db.session.commit()
    assert recovery.load_work_sets(uid, NOW - recovery.LOOKBACK) == []


def test_neglect_new_user_gets_nothing(app, alice):
    assert neglected_muscles(User.query.one().id, NOW) == []


def test_neglect_rules():
    sets = _sets(10, hours_ago=24, primary="chest", secondary=("triceps", "shoulders")) + _sets(
        10, hours_ago=24 * 8, primary="back", secondary=("biceps",), session_id=2
    )
    out = recovery._neglected(sets, NOW)
    # never trained first (canonical order), at most 3; forearms excluded; chest trained recently
    assert out == ["core", "quads", "hamstrings"]
    assert "forearms" not in out and "chest" not in out


def test_neglect_respects_weekly_target_and_orders_by_days():
    covered = [m for m in recovery.CANONICAL if m not in ("back", "hamstrings", "forearms")]
    recent = [
        ws for i, m in enumerate(covered) for ws in _sets(10, hours_ago=24, primary=m, session_id=10 + i)
    ]
    # back: 4 sets 8 days ago (< 10 target) -> neglected; hamstrings: 2 sets 10 days ago -> neglected first
    sets = recent + _sets(4, hours_ago=24 * 8, primary="back", session_id=2)
    sets += _sets(2, hours_ago=24 * 10, primary="hamstrings", session_id=3)
    assert recovery._neglected(sets, NOW) == ["hamstrings", "back"]
    # enough volume in the window -> not neglected even when last trained >= 6 days ago
    sets = recent + _sets(10, hours_ago=24 * 8, primary="back", session_id=2)
    sets += _sets(10, hours_ago=24 * 8, primary="hamstrings", session_id=3)
    assert recovery._neglected(sets, NOW) == []
