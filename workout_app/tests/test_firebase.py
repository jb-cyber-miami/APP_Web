"""Settings that make the app work behind Firebase Hosting -> Cloud Run."""

import pytest

from app import PublicURL, create_app
from tests.conftest import register


def test_session_cookie_is_named_for_firebase(client):
    # Firebase Hosting forwards only the cookie named "__session" to Cloud Run.
    resp = register(client, "alice")
    cookies = resp.headers.getlist("Set-Cookie")
    assert any(c.startswith("__session=") for c in cookies)
    assert client.get("/").status_code == 200  # the __session cookie alone keeps you signed in


RUN_APP = "http://fitlog-abc123-ue.a.run.app"  # the host Cloud Run sees behind Firebase


def _register_on(client, base_url):
    pw = "correct horse battery"
    data = {"username": "alice", "display_name": "Alice", "password": pw, "confirm": pw}
    assert client.post("/register", data=data, base_url=base_url).status_code == 302


def test_public_url_pins_links_to_the_public_domain(app):
    app.wsgi_app = PublicURL(app.wsgi_app, "https://fitlog-demo.web.app")
    client = app.test_client()
    _register_on(client, RUN_APP)
    page = client.post("/integrations/tokens", data={"name": "iPhone"}, base_url=RUN_APP).data.decode()
    assert "https://fitlog-demo.web.app/api/v1/apple/daily" in page
    assert "run.app" not in page


def test_public_url_must_be_absolute():
    with pytest.raises(RuntimeError, match="PUBLIC_URL"):
        PublicURL(lambda e, s: None, "fitlog.web.app")


def test_public_url_is_applied_from_config(monkeypatch):
    from app import config

    monkeypatch.setattr(config.TestingConfig, "PUBLIC_URL", "https://fitlog-demo.web.app")
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        client = app.test_client()
        _register_on(client, RUN_APP)
        page = client.post("/integrations/tokens", data={"name": "iPhone"}, base_url=RUN_APP).data.decode()
        assert "https://fitlog-demo.web.app/api/v1/apple/workout" in page
