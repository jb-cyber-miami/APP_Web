"""Database models.

Every user-owned row carries ``user_id``; every query that loads one goes through
``owned_or_404`` (see app/security.py) so one user can never read or change
another user's data by guessing an id.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(255), index=True)
    # Null for accounts that only ever signed in with Google.
    password_hash = db.Column(db.String(255))
    google_sub = db.Column(db.String(255), unique=True)
    timezone = db.Column(db.String(64), nullable=False, default="America/New_York")
    # Bumped on password change / "log out everywhere" to invalidate sessions.
    session_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password, method="scrypt")

    def check_password(self, password: str) -> bool:
        return bool(self.password_hash) and check_password_hash(self.password_hash, password)

    def get_id(self) -> str:
        # Embedding session_version means old cookies stop working after a bump.
        return f"{self.id}:{self.session_version}"


class GymProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    gym_size = db.Column(db.String(20), nullable=False, default="commercial")
    equipment_json = db.Column(db.Text, nullable=False, default="[]")
    goal = db.Column(db.String(40), nullable=False, default="general fitness")
    experience = db.Column(db.String(20), nullable=False, default="intermediate")
    notes = db.Column(db.String(500), nullable=False, default="")

    @property
    def equipment(self) -> list[str]:
        try:
            return list(json.loads(self.equipment_json or "[]"))
        except ValueError:
            return []

    @equipment.setter
    def equipment(self, items: list[str]) -> None:
        self.equipment_json = json.dumps(sorted(set(items)))


class Exercise(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    muscle_group = db.Column(db.String(40), nullable=False, default="")
    equipment = db.Column(db.String(60), nullable=False, default="")
    sets = db.Column(db.Integer, nullable=False, default=3)
    reps = db.Column(db.String(20), nullable=False, default="10")
    rest_seconds = db.Column(db.Integer, nullable=False, default=60)
    instructions = db.Column(db.Text, nullable=False, default="")
    # Only a validated 11-char YouTube id is ever stored, never a raw URL.
    youtube_video_id = db.Column(db.String(11))
    source = db.Column(db.String(10), nullable=False, default="manual")  # manual | ai
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class WorkoutSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    day = db.Column(db.Date, nullable=False, index=True)  # in the user's timezone
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow)  # UTC
    ended_at = db.Column(db.DateTime)  # UTC
    notes = db.Column(db.String(500), nullable=False, default="")
    # Calories attributed to this session at finish time (from Oura / Apple / manual).
    calories = db.Column(db.Integer)
    calories_source = db.Column(db.String(10))

    entries = db.relationship(
        "SessionEntry", backref="session", cascade="all, delete-orphan", order_by="SessionEntry.id"
    )

    @property
    def is_active(self) -> bool:
        return self.ended_at is None


class SessionEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("workout_session.id"), nullable=False, index=True)
    exercise_id = db.Column(db.Integer, db.ForeignKey("exercise.id", ondelete="SET NULL"))
    # Copied so history survives if the exercise is later deleted.
    exercise_name = db.Column(db.String(80), nullable=False)
    sets_done = db.Column(db.Integer, nullable=False, default=0)
    reps_done = db.Column(db.String(20), nullable=False, default="")
    weight = db.Column(db.String(20), nullable=False, default="")


class HealthSample(db.Model):
    """Calorie data pulled from Oura or pushed from Apple Health."""

    __table_args__ = (db.UniqueConstraint("user_id", "source", "kind", "external_id"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    source = db.Column(db.String(10), nullable=False)  # oura | apple
    kind = db.Column(db.String(10), nullable=False)  # daily | workout
    external_id = db.Column(db.String(128), nullable=False)
    day = db.Column(db.Date, nullable=False, index=True)
    start_at = db.Column(db.DateTime)  # UTC, workouts only
    end_at = db.Column(db.DateTime)  # UTC, workouts only
    active_kcal = db.Column(db.Integer, nullable=False, default=0)
    total_kcal = db.Column(db.Integer)
    activity = db.Column(db.String(60), nullable=False, default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Integration(db.Model):
    """OAuth connection to a third party. Tokens are encrypted at rest."""

    __table_args__ = (db.UniqueConstraint("user_id", "provider"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    provider = db.Column(db.String(20), nullable=False)
    access_token_enc = db.Column(db.Text, nullable=False)
    refresh_token_enc = db.Column(db.Text)
    expires_at = db.Column(db.DateTime)
    last_synced_at = db.Column(db.DateTime)
    last_error = db.Column(db.String(200))


class ApiToken(db.Model):
    """Personal token for the Apple Health shortcut. Only the SHA-256 is stored."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(40), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    prefix = db.Column(db.String(12), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_used_at = db.Column(db.DateTime)


class ShareLink(db.Model):
    """Public, read-only, expiring link to one day's summary."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    day = db.Column(db.Date, nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship("User")
