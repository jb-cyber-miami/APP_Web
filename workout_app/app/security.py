"""Security helpers shared by every blueprint.

Trust boundaries (details in docs/SECURITY.md):
  * Browser -> app: CSRF tokens on every form, secure cookies, strict CSP.
  * App -> database: SQLAlchemy ORM only (parameterised), ownership checks on every row.
  * Third parties (Google, Oura, Anthropic, YouTube): only reached server-side,
    their responses are validated before use and never rendered unescaped.
  * Apple Shortcut -> API: bearer token, hashed at rest, rate limited.
  * Public share pages: random 256-bit token, hashed at rest, expiring, revocable.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from flask import abort, current_app
from flask_login import current_user

from .extensions import db


# ---------------------------------------------------------------------------
# Ownership: the single gate against IDOR (insecure direct object reference)
# ---------------------------------------------------------------------------
def owned_or_404(model, object_id: int):
    """Load a row that belongs to the logged-in user, or 404.

    404 (not 403) so attackers can't tell "exists but not yours" from "doesn't exist".
    """
    obj = db.session.get(model, object_id)
    if obj is None or obj.user_id != current_user.id:
        abort(404)
    return obj


# ---------------------------------------------------------------------------
# Random tokens (share links, API tokens)
# ---------------------------------------------------------------------------
def new_token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    # Tokens are 256-bit random values, so a fast hash is appropriate
    # (no need for a slow password hash).
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Encryption at rest for third-party OAuth tokens
# ---------------------------------------------------------------------------
def _fernet() -> Fernet:
    key = current_app.config.get("TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not configured")
    raw = key.encode() if isinstance(key, str) else key
    try:
        return Fernet(raw)
    except ValueError:
        # Not a Fernet key (e.g. a random hex secret from Secret Manager):
        # derive one. Only safe for high-entropy secrets, which create_app enforces in production.
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str | None:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None


# ---------------------------------------------------------------------------
# Open-redirect protection for ?next=
# ---------------------------------------------------------------------------
def safe_next_url(target: str | None) -> str | None:
    """Only allow same-site relative paths like "/sessions/3"."""
    if not target:
        return None
    target = target.replace("\\", "/")
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


# ---------------------------------------------------------------------------
# HTTP security headers
# ---------------------------------------------------------------------------
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: https://i.ytimg.com https://lh3.googleusercontent.com",
        "frame-src https://www.youtube-nocookie.com",
        "connect-src 'self'",
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "manifest-src 'self'",
    ]
)


def apply_security_headers(response):
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if current_app.config.get("SESSION_COOKIE_SECURE"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    # Pages behind login must never be cached by shared caches.
    if current_user and current_user.is_authenticated:
        response.headers.setdefault("Cache-Control", "no-store")
    return response
