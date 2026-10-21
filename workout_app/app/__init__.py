"""Application factory."""

from __future__ import annotations

import logging
import os

from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import CONFIGS
from .extensions import csrf, db, limiter, login_manager, oauth
from .security import apply_security_headers


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.environ.get("APP_ENV", "production")
    app = Flask(__name__)
    app.config.from_object(CONFIGS[config_name])

    _check_config(app, config_name)

    hops = app.config["PROXY_FIX_HOPS"]
    if hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    logging.basicConfig(level=logging.INFO)

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.session_protection = "strong"
    oauth.init_app(app)
    _register_oauth_clients(app)

    from .auth.routes import bp as auth_bp
    from .integrations.routes import bp as integrations_bp
    from .sharing.routes import bp as sharing_bp
    from .workouts.routes import bp as workouts_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(workouts_bp)
    app.register_blueprint(integrations_bp)
    app.register_blueprint(sharing_bp)

    app.after_request(apply_security_headers)
    _register_error_handlers(app)
    _register_template_helpers(app)

    with app.app_context():
        db.create_all()

    return app


def _check_config(app: Flask, config_name: str) -> None:
    """Fail fast on unsafe configuration instead of running insecurely."""
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY must be set (see .env.example)")
    if config_name != "production":
        return
    if len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError("SECRET_KEY must be at least 32 characters in production")
    if app.config.get("OURA_CLIENT_ID") and not app.config.get("TOKEN_ENCRYPTION_KEY"):
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is required when Oura is enabled")
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        logging.getLogger(__name__).warning(
            "Using SQLite in production: data is lost on hosts with ephemeral disks (e.g. Render)."
        )


def _register_oauth_clients(app: Flask) -> None:
    if app.config.get("GOOGLE_CLIENT_ID"):
        oauth.register(
            name="google",
            client_id=app.config["GOOGLE_CLIENT_ID"],
            client_secret=app.config["GOOGLE_CLIENT_SECRET"],
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    if app.config.get("OURA_CLIENT_ID"):
        oauth.register(
            name="oura",
            client_id=app.config["OURA_CLIENT_ID"],
            client_secret=app.config["OURA_CLIENT_SECRET"],
            authorize_url="https://cloud.ouraring.com/oauth/authorize",
            access_token_url="https://api.ouraring.com/oauth/token",  # nosec B106 # noqa: S106 - a URL, not a secret
            client_kwargs={"scope": "daily workout"},
        )


def _register_error_handlers(app: Flask) -> None:
    def handler(code: int, title: str, message: str):
        def _handle(_err):
            return render_template("errors/error.html", code=code, title=title, message=message), code

        return _handle

    app.register_error_handler(400, handler(400, "Bad request", "Something about that request wasn't right."))
    app.register_error_handler(404, handler(404, "Not found", "That page doesn't exist."))
    app.register_error_handler(413, handler(413, "Too large", "That request was too large."))
    app.register_error_handler(
        429, handler(429, "Slow down", "Too many attempts. Please wait a minute and try again.")
    )
    # Never leak stack traces to the browser; they go to the server log only.
    app.register_error_handler(500, handler(500, "Server error", "Something went wrong on our side."))

    from flask_wtf.csrf import CSRFError

    app.register_error_handler(
        CSRFError, handler(400, "Session expired", "Please go back, refresh the page and try again.")
    )


def _register_template_helpers(app: Flask) -> None:
    from .timeutil import to_local

    @app.template_filter("localtime")
    def localtime(dt, tz_name, fmt="%I:%M %p"):
        local = to_local(dt, tz_name)
        return local.strftime(fmt).lstrip("0") if local else ""

    @app.context_processor
    def inject_features():
        return {
            "google_enabled": bool(app.config.get("GOOGLE_CLIENT_ID")),
            "oura_enabled": bool(app.config.get("OURA_CLIENT_ID")),
            "ai_enabled": bool(app.config.get("ANTHROPIC_API_KEY")),
        }


@login_manager.user_loader
def load_user(user_id: str):
    from .models import User

    try:
        raw_id, raw_version = user_id.split(":", 1)
        user = db.session.get(User, int(raw_id))
    except (ValueError, TypeError):
        return None
    if user is None or str(user.session_version) != raw_version:
        return None
    return user
