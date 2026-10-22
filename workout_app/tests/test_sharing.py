import re
from datetime import date, timedelta

from app.extensions import db
from app.models import Exercise, ShareLink, User, WorkoutSession, utcnow
from app.timeutil import today_for


def _share(client, day):
    page = client.post("/share", data={"day": day.isoformat()}).data.decode()
    return re.search(r'id="share-url" value="http://localhost(/s/[^"]+)"', page).group(1)


def test_share_workout_day(alice, app):
    alice.post("/exercises/new", data={"name": "Pull-up", "sets": 3, "reps": "8", "rest_seconds": 60})
    ex = Exercise.query.one()
    alice.post("/sessions/start")
    ws = WorkoutSession.query.one()
    alice.post(f"/sessions/{ws.id}", data={"exercise_id": ex.id, "sets_done": 3, "reps_done": "8"})
    alice.post(f"/sessions/{ws.id}/finish")
    alice.post(f"/sessions/{ws.id}/calories", data={"calories": 420})

    path = _share(alice, ws.day)
    stranger = app.test_client()
    resp = stranger.get(path)
    assert resp.status_code == 200
    assert b"Alice trained today" in resp.data
    assert b"Pull-up" in resp.data and b"420" in resp.data
    assert resp.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert b"alice" not in resp.data.replace(b"Alice", b"")  # username is never exposed


def test_share_skipped_day(alice, app):
    day = today_for(User.query.one().timezone) - timedelta(days=1)
    resp = app.test_client().get(_share(alice, day))
    assert "didn't go to the gym" in resp.data.decode()


def test_token_is_hashed_and_revocable(alice, app):
    path = _share(alice, date.today() - timedelta(days=1))
    raw = path.rsplit("/", 1)[1]
    link = ShareLink.query.one()
    assert link.token_hash != raw
    alice.post(f"/shares/{link.id}/revoke")
    assert app.test_client().get(path).status_code == 404


def test_expired_link_404(alice, app):
    path = _share(alice, date.today() - timedelta(days=1))
    link = ShareLink.query.one()
    link.expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert app.test_client().get(path).status_code == 404


def test_unknown_token_404(client):
    assert client.get("/s/nope").status_code == 404
    assert client.get("/s/" + "a" * 200).status_code == 404


def test_cannot_share_future_day(alice):
    resp = alice.post("/share", data={"day": (date.today() + timedelta(days=3)).isoformat()})
    assert resp.status_code == 302
    assert ShareLink.query.count() == 0


def test_cannot_revoke_someone_elses_link(alice, bob):
    _share(alice, date.today() - timedelta(days=1))
    link = ShareLink.query.one()
    assert bob.post(f"/shares/{link.id}/revoke").status_code == 404
    assert link.revoked is False
