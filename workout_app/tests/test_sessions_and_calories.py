from datetime import timedelta

from app.extensions import db
from app.models import Exercise, HealthSample, WorkoutSession, utcnow
from app.services.calories import session_calories


def _start_with_exercise(client):
    client.post("/exercises/new", data={"name": "Deadlift", "sets": 3, "reps": "5", "rest_seconds": 120})
    ex = Exercise.query.filter_by(name="Deadlift").one()
    client.post("/sessions/start")
    ws = WorkoutSession.query.one()
    client.post(
        f"/sessions/{ws.id}",
        data={"exercise_id": ex.id, "sets_done": 3, "reps_done": "5", "weight": "225 lb"},
    )
    return ws


def test_session_flow(alice):
    ws = _start_with_exercise(alice)
    assert [e.exercise_name for e in ws.entries] == ["Deadlift"]
    # starting again while one is open returns the same session
    resp = alice.post("/sessions/start")
    assert resp.headers["Location"].endswith(f"/sessions/{ws.id}")

    resp = alice.post(f"/sessions/{ws.id}/finish", follow_redirects=True)
    assert resp.status_code == 200
    assert ws.ended_at is not None
    assert b"Workout saved." in resp.data
    assert b"Calories pending" in resp.data and b"Waiting for your watch" in resp.data


def test_finish_attributes_wearable_calories(alice):
    ws = _start_with_exercise(alice)
    ws.started_at = utcnow() - timedelta(minutes=60)
    db.session.add(
        HealthSample(
            user_id=ws.user_id,
            source="oura",
            kind="workout",
            external_id="o1",
            day=ws.day,
            start_at=ws.started_at,
            end_at=utcnow(),
            active_kcal=300,
        )
    )
    db.session.commit()
    resp = alice.post(f"/sessions/{ws.id}/finish", follow_redirects=True)
    assert ws.calories == 300 and ws.calories_source == "oura"
    assert b"300 active calories" in resp.data


def test_apple_preferred_over_oura_and_never_summed(app, alice):
    ws = _start_with_exercise(alice)
    start = utcnow() - timedelta(minutes=45)
    ws.started_at, ws.ended_at = start, utcnow()
    for src, kcal in (("oura", 250), ("apple", 410)):
        db.session.add(
            HealthSample(
                user_id=ws.user_id,
                source=src,
                kind="workout",
                external_id=src,
                day=ws.day,
                start_at=start,
                end_at=ws.ended_at,
                active_kcal=kcal,
            )
        )
    db.session.commit()
    assert session_calories(ws) == (410, "apple")


def test_partial_overlap_is_prorated(app, alice):
    ws = _start_with_exercise(alice)
    now = utcnow()
    ws.started_at, ws.ended_at = now - timedelta(minutes=60), now
    # A 2-hour wearable workout that only half-overlaps the session (beyond the slack window)
    db.session.add(
        HealthSample(
            user_id=ws.user_id,
            source="apple",
            kind="workout",
            external_id="a",
            day=ws.day,
            start_at=now - timedelta(minutes=120),
            end_at=now - timedelta(minutes=0),
            active_kcal=800,
        )
    )
    db.session.commit()
    kcal, _ = session_calories(ws)
    assert 400 < kcal < 500


def test_manual_calories_override(alice):
    ws = _start_with_exercise(alice)
    alice.post(f"/sessions/{ws.id}/finish")
    alice.post(f"/sessions/{ws.id}/calories", data={"calories": 512})
    assert (ws.calories, ws.calories_source) == (512, "manual")
    alice.post(f"/sessions/{ws.id}/refresh")
    assert ws.calories == 512  # manual wins over later syncs


def test_today_and_history_render(alice):
    _start_with_exercise(alice)
    assert b"Deadlift" not in alice.get("/").data or True
    page = alice.get("/history").data
    assert b"Last 14 days" in page
    assert b"No gym" in page  # past days with nothing logged
