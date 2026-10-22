from app.extensions import db
from app.models import (
    AIUsage,
    DailyPlan,
    EntryPlan,
    Exercise,
    ExerciseMuscles,
    SessionEntry,
    SetLog,
    TrainerSettings,
    User,
    WorkoutSession,
    utcnow,
)

NEW_TABLES = (TrainerSettings, ExerciseMuscles, EntryPlan, SetLog, DailyPlan, AIUsage)


def _fill(client, username):
    uid = User.query.filter_by(username=username).one().id
    client.post("/settings/trainer", data={"unit": "kg", "default_minutes": 40})
    client.post("/plan/start")
    ws = WorkoutSession.query.filter_by(user_id=uid).one()
    entry = SessionEntry.query.filter_by(session_id=ws.id).first()
    client.post(f"/sessions/{ws.id}/entries/{entry.id}/sets", data={"set_index": 0, "reps": 8, "seconds": 30})
    db.session.add(AIUsage(user_id=uid, day=utcnow().date(), calls=1))
    db.session.commit()
    return uid


def test_delete_account_removes_rows_in_all_new_tables(alice, bob):
    alice_id = _fill(alice, "alice")
    bob_id = _fill(bob, "bob")
    for model in NEW_TABLES:
        assert model.query.filter_by(user_id=alice_id).count() > 0, model.__name__

    resp = alice.post("/settings/delete-account", data={"confirm": "alice"})
    assert resp.status_code == 302
    assert db.session.get(User, alice_id) is None
    for model in NEW_TABLES + (Exercise, WorkoutSession):
        assert model.query.filter_by(user_id=alice_id).count() == 0, model.__name__
    # the other user's data is untouched
    for model in NEW_TABLES:
        assert model.query.filter_by(user_id=bob_id).count() > 0, model.__name__
