"""Per-user coach preferences (``TrainerSettings``). Absence of a row means defaults."""

from __future__ import annotations

from typing import Literal

from ..extensions import db
from ..models import TrainerSettings

DEFAULT_UNIT = "lb"
DEFAULT_MINUTES = 45


def _row(user_id: int) -> TrainerSettings | None:
    return TrainerSettings.query.filter_by(user_id=user_id).first()


def unit_for(user_id: int) -> Literal["lb", "kg"]:
    row = _row(user_id)
    return "kg" if row is not None and row.unit == "kg" else "lb"


def default_minutes_for(user_id: int) -> int:
    row = _row(user_id)
    if row is None or not row.default_minutes:
        return DEFAULT_MINUTES
    return max(10, min(120, int(row.default_minutes)))


def get_or_create_settings(user_id: int) -> TrainerSettings:
    """POST paths only: adds a row with defaults if missing (no commit)."""
    row = _row(user_id)
    if row is None:
        row = TrainerSettings(user_id=user_id, unit=DEFAULT_UNIT, default_minutes=DEFAULT_MINUTES)
        db.session.add(row)
    return row
