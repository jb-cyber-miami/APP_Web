from app.models import User
from tests.conftest import register


def test_register_hashes_password_and_logs_in(client):
    resp = register(client, "Alice")
    assert resp.status_code == 302
    user = User.query.filter_by(username="alice").one()
    assert user.password_hash.startswith("scrypt:")
    assert "correct horse" not in user.password_hash
    assert client.get("/").status_code == 200


def test_duplicate_username_rejected(client, app):
    register(client, "alice")
    other = app.test_client()
    resp = register(other, "ALICE")
    assert resp.status_code == 200
    assert b"That username is taken" in resp.data


def test_weak_password_rejected(client):
    resp = register(client, "alice", password="short")
    assert resp.status_code == 200
    assert User.query.count() == 0


def test_login_wrong_password_is_generic(client, app):
    register(client, "alice")
    fresh = app.test_client()
    bad_user = fresh.post("/login", data={"username": "nobody", "password": "whatever123"})
    bad_pass = fresh.post("/login", data={"username": "alice", "password": "wrong password"})
    assert b"Wrong username or password." in bad_user.data
    assert b"Wrong username or password." in bad_pass.data


def test_login_blocks_open_redirect(client, app):
    register(client, "alice")
    fresh = app.test_client()
    resp = fresh.post(
        "/login?next=https://evil.example/phish",
        data={"username": "alice", "password": "correct horse battery"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"
    resp = fresh.post(
        "/login?next=//evil.example", data={"username": "alice", "password": "correct horse battery"}
    )
    assert resp.headers["Location"] == "/"


def test_login_allows_local_next(client, app):
    register(client, "alice")
    fresh = app.test_client()
    resp = fresh.post("/login?next=/history", data={"username": "alice", "password": "correct horse battery"})
    assert resp.headers["Location"] == "/history"


def test_logout_requires_post(alice):
    assert alice.get("/logout").status_code == 405
    assert alice.post("/logout").status_code == 302
    assert alice.get("/").status_code == 302


def test_password_change_signs_out_other_devices(alice, app):
    other_device = app.test_client()
    other_device.post("/login", data={"username": "alice", "password": "correct horse battery"})
    assert other_device.get("/").status_code == 200

    resp = alice.post(
        "/settings/password",
        data={
            "pw-current": "correct horse battery",
            "pw-password": "a brand new secret",
            "pw-confirm": "a brand new secret",
        },
    )
    assert resp.status_code == 302
    assert alice.get("/").status_code == 200  # this device stays signed in
    assert other_device.get("/").status_code == 302  # the other one is kicked out


def test_delete_account_removes_everything(alice):
    alice.post("/exercises/new", data={"name": "Squat", "sets": 3, "reps": "5", "rest_seconds": 90})
    resp = alice.post("/settings/delete-account", data={"confirm": "alice"})
    assert resp.status_code == 302
    assert User.query.count() == 0


def test_google_routes_404_when_not_configured(client):
    assert client.get("/login/google").status_code == 404
