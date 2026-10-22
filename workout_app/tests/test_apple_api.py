import re
from datetime import date, timedelta

import pytest

from app.models import ApiToken, HealthSample, WorkoutSession, utcnow


@pytest.fixture
def token(alice):
    page = alice.post("/integrations/tokens", data={"name": "iPhone"}).data.decode()
    raw = re.search(r'id="new-token" value="(wk_[^"]+)"', page).group(1)
    stored = ApiToken.query.one()
    assert stored.token_hash != raw and raw not in stored.token_hash  # only the hash is stored
    return raw


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_requires_token(client):
    assert client.post("/api/v1/apple/daily", json={}).status_code == 401
    resp = client.post("/api/v1/apple/daily", json={}, headers=_auth("wk_not-a-real-token"))
    assert resp.status_code == 401


def test_daily_upsert(client, token):
    today = date.today().isoformat()
    for kcal in (300, 455.6):
        resp = client.post(
            "/api/v1/apple/daily", json={"day": today, "active_kcal": kcal}, headers=_auth(token)
        )
        assert resp.status_code == 200, resp.json
    sample = HealthSample.query.one()
    assert (sample.source, sample.kind, sample.active_kcal) == ("apple", "daily", 456)


@pytest.mark.parametrize(
    "payload",
    [
        {"day": "not-a-date", "active_kcal": 1},
        {"day": date.today().isoformat(), "active_kcal": -5},
        {"day": date.today().isoformat(), "active_kcal": 1e9},
        {"day": (date.today() - timedelta(days=400)).isoformat(), "active_kcal": 10},
    ],
)
def test_daily_validation(client, token, payload):
    resp = client.post("/api/v1/apple/daily", json=payload, headers=_auth(token))
    assert resp.status_code == 422


def test_workout_requires_timezone_and_sane_window(client, token):
    now = utcnow()
    resp = client.post(
        "/api/v1/apple/workout",
        headers=_auth(token),
        json={"start": now.isoformat(), "end": (now + timedelta(hours=1)).isoformat(), "active_kcal": 100},
    )
    assert resp.status_code == 422
    resp = client.post(
        "/api/v1/apple/workout",
        headers=_auth(token),
        json={
            "start": now.isoformat() + "Z",
            "end": (now + timedelta(hours=9)).isoformat() + "Z",
            "active_kcal": 100,
        },
    )
    assert resp.status_code == 422


def test_workout_updates_session(alice, token):
    alice.post("/sessions/start")
    ws = WorkoutSession.query.one()
    alice.post(f"/sessions/{ws.id}/finish")
    resp = alice.post(
        "/api/v1/apple/workout",
        headers=_auth(token),
        json={
            "start": ws.started_at.isoformat() + "Z",
            "end": (ws.ended_at + timedelta(seconds=1)).isoformat() + "Z",
            "active_kcal": 333,
            "activity": "Strength",
            "session_id": ws.id,
        },
    )
    assert resp.status_code == 200
    assert resp.json["session_calories"] == 333
    assert (ws.calories, ws.calories_source) == (333, "apple")


def test_token_cannot_touch_other_users_session(app, alice, bob, token):
    bob.post("/sessions/start")
    bob_ws = WorkoutSession.query.one()
    bob.post(f"/sessions/{bob_ws.id}/finish")
    resp = alice.post(
        "/api/v1/apple/workout",
        headers=_auth(token),
        json={
            "start": bob_ws.started_at.isoformat() + "Z",
            "end": (bob_ws.ended_at + timedelta(seconds=1)).isoformat() + "Z",
            "active_kcal": 999,
            "session_id": bob_ws.id,
        },
    )
    assert resp.status_code == 200
    assert "session_calories" not in resp.json
    assert bob_ws.calories is None


def test_revoked_token_stops_working(alice, token):
    tid = ApiToken.query.one().id
    alice.post(f"/integrations/tokens/{tid}/delete")
    resp = alice.post(
        "/api/v1/apple/daily", json={"day": date.today().isoformat(), "active_kcal": 1}, headers=_auth(token)
    )
    assert resp.status_code == 401
