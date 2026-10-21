from datetime import date

import pytest

from app.extensions import db
from app.models import HealthSample, Integration, User
from app.security import decrypt
from app.services import oura


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.fixture
def integration(alice):
    user = User.query.one()
    integ = Integration(user_id=user.id, provider="oura", access_token_enc="")
    oura.save_tokens(integ, {"access_token": "acc-1", "refresh_token": "ref-1", "expires_in": 3600})
    db.session.add(integ)
    db.session.commit()
    return integ


def test_tokens_encrypted_at_rest(integration):
    assert "acc-1" not in integration.access_token_enc
    assert decrypt(integration.access_token_enc) == "acc-1"
    assert decrypt(integration.refresh_token_enc) == "ref-1"


def test_sync_upserts_daily_and_workouts(monkeypatch, integration):
    today = date.today().isoformat()
    responses = {
        "daily_activity": {
            "data": [{"id": "d1", "day": today, "active_calories": 612, "total_calories": 2650}],
            "next_token": None,
        },
        "workout": {
            "data": [
                {
                    "id": "w1",
                    "day": today,
                    "activity": "strengthTraining",
                    "calories": 280,
                    "start_datetime": f"{today}T10:00:00-04:00",
                    "end_datetime": f"{today}T11:00:00-04:00",
                }
            ],
            "next_token": None,
        },
    }
    seen_headers = []

    def fake_get(url, headers, params, timeout):
        seen_headers.append(headers["Authorization"])
        return FakeResp(200, responses[url.rsplit("/", 1)[1]])

    monkeypatch.setattr(oura.requests, "get", fake_get)
    assert oura.sync(integration) == 2
    assert oura.sync(integration) == 2  # idempotent
    assert HealthSample.query.count() == 2
    w = HealthSample.query.filter_by(kind="workout").one()
    assert w.start_at.hour == 14  # stored as UTC
    assert w.active_kcal == 280
    assert set(seen_headers) == {"Bearer acc-1"}
    assert integration.last_error is None


def test_sync_refreshes_on_401(monkeypatch, integration):
    calls = {"n": 0}

    def fake_get(url, headers, params, timeout):
        calls["n"] += 1
        if headers["Authorization"] == "Bearer acc-1":
            return FakeResp(401, {})
        return FakeResp(200, {"data": [], "next_token": None})

    def fake_post(url, data, timeout):
        assert data["grant_type"] == "refresh_token" and data["refresh_token"] == "ref-1"
        return FakeResp(200, {"access_token": "acc-2", "refresh_token": "ref-2", "expires_in": 3600})

    monkeypatch.setattr(oura.requests, "get", fake_get)
    monkeypatch.setattr(oura.requests, "post", fake_post)
    oura.sync(integration)
    assert decrypt(integration.access_token_enc) == "acc-2"
    assert decrypt(integration.refresh_token_enc) == "ref-2"  # single-use refresh token rotated


def test_sync_records_error(monkeypatch, integration):
    monkeypatch.setattr(oura.requests, "get", lambda *a, **k: FakeResp(500, {}))
    with pytest.raises(oura.OuraError):
        oura.sync(integration)
    assert integration.last_error
