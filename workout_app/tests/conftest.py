import re

import pytest
from flask import g

from app import create_app
from app.extensions import db


@pytest.fixture
def app():
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = False

    # Tests hold one app context open (so they can query the DB), which Flask then
    # reuses for every request. Drop Flask-Login's per-request user cache so separate
    # test clients don't see each other's login.
    @app.before_request
    def _isolate_login_cache():
        g.pop("_login_user", None)

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def register(client, username="alice", password="correct horse battery", name=None):
    return client.post(
        "/register",
        data={
            "username": username,
            "display_name": name or username.title(),
            "password": password,
            "confirm": password,
        },
        follow_redirects=False,
    )


@pytest.fixture
def alice(client):
    resp = register(client, "alice")
    assert resp.status_code == 302
    return client


@pytest.fixture
def bob(app):
    other = app.test_client()
    register(other, "bob")
    return other


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" (?:type="hidden" )?value="([^"]+)"', html)
    assert match, "no csrf token in page"
    return match.group(1)
