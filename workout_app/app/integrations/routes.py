"""Oura connection, Apple Health shortcut tokens, and the Apple Health ingest API."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from pydantic import BaseModel, Field, ValidationError, model_validator

from ..extensions import csrf, db, limiter, oauth
from ..forms import ApiTokenForm
from ..models import ApiToken, HealthSample, Integration, User, WorkoutSession, utcnow
from ..security import hash_token, new_token, owned_or_404
from ..services import calories, oura
from ..timeutil import to_local, to_utc_naive

bp = Blueprint("integrations", __name__)
log = logging.getLogger(__name__)

TOKEN_PREFIX = "wk_"  # nosec B105 # noqa: S105 - public prefix, not a secret


@bp.route("/integrations")
@login_required
def index():
    integration = Integration.query.filter_by(user_id=current_user.id, provider="oura").first()
    tokens = ApiToken.query.filter_by(user_id=current_user.id).order_by(ApiToken.created_at.desc()).all()
    return render_template(
        "integrations/index.html",
        oura_integration=integration,
        tokens=tokens,
        token_form=ApiTokenForm(),
        new_token=None,
        api_base=url_for("integrations.index", _external=True).rsplit("/integrations", 1)[0],
    )


# ---------------------------------------------------------------------------
# Oura (OAuth2)
# ---------------------------------------------------------------------------
@bp.route("/integrations/oura/connect")
@login_required
def oura_connect():
    client = oauth.create_client("oura")
    if client is None:
        abort(404)
    return client.authorize_redirect(url_for("integrations.oura_callback", _external=True))


@bp.route("/integrations/oura/callback")
@login_required
def oura_callback():
    client = oauth.create_client("oura")
    if client is None:
        abort(404)
    try:
        token = client.authorize_access_token()  # Authlib verifies the state parameter
    except Exception:  # noqa: BLE001 - any OAuth failure means "not connected"
        log.warning("Oura OAuth callback failed", exc_info=True)
        flash("Couldn't connect to Oura. Please try again.", "error")
        return redirect(url_for("integrations.index"))

    integration = Integration.query.filter_by(user_id=current_user.id, provider="oura").first()
    if integration is None:
        # Empty placeholder; save_tokens() stores the encrypted token right below.
        integration = Integration(user_id=current_user.id, provider="oura", access_token_enc="")  # nosec B106
        db.session.add(integration)
    oura.save_tokens(integration, token)
    db.session.commit()
    try:
        oura.sync(integration, days=14)
        flash("Oura connected and synced.", "success")
    except oura.OuraError as exc:
        flash(f"Oura connected, but the first sync failed: {exc}", "error")
    return redirect(url_for("integrations.index"))


@bp.route("/integrations/oura/sync", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def oura_sync():
    integration = Integration.query.filter_by(user_id=current_user.id, provider="oura").first_or_404()
    try:
        n = oura.sync(integration, days=7)
        flash(f"Synced {n} Oura records.", "success")
    except oura.OuraError as exc:
        flash(str(exc), "error")
    return redirect(request.referrer if _same_origin(request.referrer) else url_for("integrations.index"))


@bp.route("/integrations/oura/disconnect", methods=["POST"])
@login_required
def oura_disconnect():
    integration = Integration.query.filter_by(user_id=current_user.id, provider="oura").first_or_404()
    oura.revoke(integration)
    db.session.delete(integration)
    db.session.commit()
    flash("Oura disconnected.", "success")
    return redirect(url_for("integrations.index"))


def _same_origin(url: str | None) -> bool:
    return bool(url) and url.startswith(request.host_url)


# ---------------------------------------------------------------------------
# Apple Health shortcut tokens
# ---------------------------------------------------------------------------
@bp.route("/integrations/tokens", methods=["POST"])
@login_required
def token_create():
    form = ApiTokenForm()
    if not form.validate_on_submit():
        flash("Give the token a name.", "error")
        return redirect(url_for("integrations.index"))
    if ApiToken.query.filter_by(user_id=current_user.id).count() >= 5:
        flash("You can have at most 5 tokens. Delete one first.", "error")
        return redirect(url_for("integrations.index"))
    raw = new_token(TOKEN_PREFIX)
    db.session.add(
        ApiToken(
            user_id=current_user.id,
            name=form.name.data.strip(),
            token_hash=hash_token(raw),
            prefix=raw[:10],
        )
    )
    db.session.commit()
    integration = Integration.query.filter_by(user_id=current_user.id, provider="oura").first()
    tokens = ApiToken.query.filter_by(user_id=current_user.id).order_by(ApiToken.created_at.desc()).all()
    # The raw token is shown exactly once and never stored.
    return render_template(
        "integrations/index.html",
        oura_integration=integration,
        tokens=tokens,
        token_form=ApiTokenForm(),
        new_token=raw,
        api_base=request.host_url.rstrip("/"),
    )


@bp.route("/integrations/tokens/<int:token_id>/delete", methods=["POST"])
@login_required
def token_delete(token_id: int):
    token = owned_or_404(ApiToken, token_id)
    db.session.delete(token)
    db.session.commit()
    flash("Token revoked.", "success")
    return redirect(url_for("integrations.index"))


# ---------------------------------------------------------------------------
# Apple Health ingest API (called by the iPhone Shortcut)
# ---------------------------------------------------------------------------
def _token_key() -> str:
    auth = request.headers.get("Authorization", "")
    return "tok:" + hash_token(auth)[:16] if auth else "ip:" + (request.remote_addr or "")


def api_token_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer " + TOKEN_PREFIX):
            return jsonify(error="missing or malformed bearer token"), 401
        token = ApiToken.query.filter_by(token_hash=hash_token(auth[len("Bearer ") :].strip())).first()
        if token is None:
            return jsonify(error="invalid token"), 401
        token.last_used_at = utcnow()
        g.api_user = db.session.get(User, token.user_id)
        return fn(*args, **kwargs)

    return wrapper


class DailyPayload(BaseModel):
    day: date
    active_kcal: float = Field(ge=0, le=20000)
    total_kcal: float | None = Field(default=None, ge=0, le=30000)

    @model_validator(mode="after")
    def _recent(self):
        if abs((date.today() - self.day).days) > 31:
            raise ValueError("day must be within the last 31 days")
        return self


class WorkoutPayload(BaseModel):
    start: datetime
    end: datetime
    active_kcal: float = Field(ge=0, le=10000)
    activity: str = Field(default="", max_length=60)
    session_id: int | None = None

    @model_validator(mode="after")
    def _window(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("start and end must include a timezone offset or Z")
        if not timedelta(0) < self.end - self.start <= timedelta(hours=6):
            raise ValueError("end must be after start and at most 6 hours later")
        return self


def _payload(model):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify(error="send a JSON object"), 400)
    try:
        return model.model_validate(data), None
    except ValidationError as exc:
        errors = [{"field": ".".join(map(str, e["loc"])), "msg": e["msg"]} for e in exc.errors()]
        return None, (jsonify(error="invalid payload", details=errors), 422)


def _upsert(user_id: int, kind: str, external_id: str, **fields) -> HealthSample:
    sample = HealthSample.query.filter_by(
        user_id=user_id, source="apple", kind=kind, external_id=external_id
    ).first()
    if sample is None:
        sample = HealthSample(user_id=user_id, source="apple", kind=kind, external_id=external_id)
        db.session.add(sample)
    for key, value in fields.items():
        setattr(sample, key, value)
    return sample


@bp.route("/api/v1/apple/daily", methods=["POST"])
@csrf.exempt  # token-authenticated, no cookies involved
@limiter.limit("60 per hour", key_func=_token_key)
@api_token_required
def apple_daily():
    payload, error = _payload(DailyPayload)
    if error:
        return error
    _upsert(
        g.api_user.id,
        "daily",
        payload.day.isoformat(),
        day=payload.day,
        active_kcal=round(payload.active_kcal),
        total_kcal=round(payload.total_kcal) if payload.total_kcal is not None else None,
    )
    db.session.commit()
    return jsonify(ok=True, day=payload.day.isoformat(), active_kcal=round(payload.active_kcal))


@bp.route("/api/v1/apple/workout", methods=["POST"])
@csrf.exempt  # token-authenticated, no cookies involved
@limiter.limit("60 per hour", key_func=_token_key)
@api_token_required
def apple_workout():
    payload, error = _payload(WorkoutPayload)
    if error:
        return error
    user = g.api_user
    start, end = to_utc_naive(payload.start), to_utc_naive(payload.end)
    local_day = to_local(start, user.timezone).date()
    _upsert(
        user.id,
        "workout",
        f"{start.isoformat()}/{end.isoformat()}",
        day=local_day,
        start_at=start,
        end_at=end,
        active_kcal=round(payload.active_kcal),
        activity=payload.activity.strip(),
    )
    db.session.flush()

    result = {"ok": True, "active_kcal": round(payload.active_kcal)}
    if payload.session_id:
        ws = db.session.get(WorkoutSession, payload.session_id)
        if ws is not None and ws.user_id == user.id and ws.calories_source != "manual":
            kcal, source = calories.session_calories(ws)
            if kcal is not None:
                ws.calories, ws.calories_source = kcal, source
                result["session_calories"] = kcal
    db.session.commit()
    return jsonify(result)
