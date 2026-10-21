"""Configuration.

Every secret comes from the environment (see .env.example). Nothing secret is
ever committed to the repository.
"""

import os
from datetime import timedelta


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///workout.db")
    # Render / Heroku hand out "postgres://", SQLAlchemy 2 wants an explicit driver.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # --- Session cookie hardening -------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", True)
    # Lax (not Strict) so the cookie survives the redirect back from Google/Oura.
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    # --- Request limits ------------------------------------------------------
    MAX_CONTENT_LENGTH = 256 * 1024  # 256 KB: no uploads in this app

    # --- CSRF ------------------------------------------------------------------
    WTF_CSRF_TIME_LIMIT = None  # token lives as long as the session

    # --- Rate limiting -------------------------------------------------------
    # Use redis://... in production when running more than one worker.
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True

    # --- Third-party integrations (all optional) ----------------------------
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

    OURA_CLIENT_ID = os.environ.get("OURA_CLIENT_ID")
    OURA_CLIENT_SECRET = os.environ.get("OURA_CLIENT_SECRET")

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
    AI_MODEL = os.environ.get("AI_MODEL", "claude-opus-5-5")
    # Hard per-user daily cap on Claude calls (AIUsage table); the limiter is per-worker memory.
    AI_DAILY_CALLS = int(os.environ.get("AI_DAILY_CALLS", "25"))
    AI_MAX_PLAN_GENERATIONS = 5

    YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

    # Fernet key used to encrypt OAuth tokens at rest.
    TOKEN_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY")

    DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "America/New_York")
    SHARE_LINK_DAYS = int(os.environ.get("SHARE_LINK_DAYS", "7"))

    # Trust X-Forwarded-* from exactly this many proxies (Render = 1).
    PROXY_FIX_HOPS = int(os.environ.get("PROXY_FIX_HOPS", "1"))


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    SECRET_KEY = Config.SECRET_KEY or "dev-only-insecure-key"


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"  # nosec B105 # noqa: S105 - tests only
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    RATELIMIT_ENABLED = False
    TOKEN_ENCRYPTION_KEY = "M1GXbhFfEM4x5Vl4n8LqkHKqhAK-3ZFdt1XUzMU7WsI="  # nosec B105 # noqa: S105 - tests only
    PROXY_FIX_HOPS = 0


CONFIGS = {
    "development": DevelopmentConfig,
    "production": Config,
    "testing": TestingConfig,
}
