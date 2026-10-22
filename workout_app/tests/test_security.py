import pytest

from app import create_app
from app.extensions import db
from app.models import Exercise, WorkoutSession
from app.security import safe_next_url


def test_security_headers(client):
    resp = client.get("/login")
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_authenticated_pages_not_cached(alice):
    assert alice.get("/").headers["Cache-Control"] == "no-store"


def test_csrf_is_enforced():
    app = create_app("testing")  # CSRF on (the default)
    with app.app_context():
        c = app.test_client()
        resp = c.post("/register", data={"username": "eve", "password": "x" * 12, "confirm": "x" * 12})
        assert resp.status_code == 400
        db.drop_all()


@pytest.mark.parametrize(
    "target,expected",
    [
        ("/history", "/history"),
        ("https://evil.example", None),
        ("//evil.example", None),
        ("/\\evil.example", None),
        ("javascript:alert(1)", None),
        ("", None),
    ],
)
def test_safe_next_url(target, expected):
    assert safe_next_url(target) == expected


def test_users_cannot_touch_each_others_data(alice, bob):
    alice.post("/exercises/new", data={"name": "Bench", "sets": 3, "reps": "8", "rest_seconds": 90})
    ex = Exercise.query.filter_by(name="Bench").one()
    alice.post("/sessions/start")
    ws = WorkoutSession.query.one()

    assert bob.get(f"/exercises/{ex.id}/edit").status_code == 404
    assert bob.post(f"/exercises/{ex.id}/delete").status_code == 404
    assert bob.get(f"/sessions/{ws.id}").status_code == 404
    assert bob.post(f"/sessions/{ws.id}/delete").status_code == 404
    assert bob.post(f"/sessions/{ws.id}/calories", data={"calories": 1}).status_code == 404
    assert db.session.get(Exercise, ex.id) is not None
    assert db.session.get(WorkoutSession, ws.id) is not None


def test_cannot_log_someone_elses_exercise(alice, bob):
    bob.post("/exercises/new", data={"name": "Bob Curl", "sets": 3, "reps": "8", "rest_seconds": 60})
    bob_ex = Exercise.query.filter_by(name="Bob Curl").one()
    alice.post("/sessions/start")
    ws = WorkoutSession.query.one()
    resp = alice.post(f"/sessions/{ws.id}", data={"exercise_id": bob_ex.id, "sets_done": 3})
    assert resp.status_code == 200  # rejected by form validation (not in her library)
    assert ws.entries == []


def test_html_is_escaped(alice):
    alice.post(
        "/exercises/new",
        data={"name": "<script>alert(1)</script>", "sets": 3, "reps": "8", "rest_seconds": 60},
    )
    page = alice.get("/exercises").data
    assert b"<script>alert(1)</script>" not in page
    assert b"&lt;script&gt;" in page


def test_production_refuses_weak_secret(monkeypatch):
    from app import config

    monkeypatch.setattr(config.Config, "SECRET_KEY", "short")
    with pytest.raises(RuntimeError, match="32 characters"):
        create_app("production")


def test_encryption_accepts_host_generated_secret(app):
    from app.security import decrypt, encrypt

    # A random secret (e.g. from Secret Manager) is not a Fernet key.
    app.config["TOKEN_ENCRYPTION_KEY"] = "q1Zr8+Xk2/9vL0aPq3sT6uW8yB1cD4eF7gH0jK3mN5o="
    token = encrypt("oura-access-token")
    assert "oura-access-token" not in token
    assert decrypt(token) == "oura-access-token"


def test_production_refuses_short_encryption_key(monkeypatch):
    from app import config

    monkeypatch.setattr(config.Config, "SECRET_KEY", "s" * 64)
    monkeypatch.setattr(config.Config, "TOKEN_ENCRYPTION_KEY", "short")
    with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY"):
        create_app("production")
