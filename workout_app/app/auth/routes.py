"""Sign up, sign in (password or Google), settings."""

from __future__ import annotations

import logging
import re
import secrets

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db, limiter, oauth
from ..forms import ChangePasswordForm, LoginForm, RegisterForm, SettingsForm, TrainerSettingsForm
from ..models import (
    AIUsage,
    ApiToken,
    DailyPlan,
    EntryPlan,
    Exercise,
    ExerciseMuscles,
    GymProfile,
    HealthSample,
    Integration,
    SetLog,
    ShareLink,
    TrainerSettings,
    User,
    WorkoutSession,
    utcnow,
)
from ..security import safe_next_url
from ..services import prefs

bp = Blueprint("auth", __name__)
log = logging.getLogger(__name__)

# Compared against when the username doesn't exist, so a failed login takes
# the same time either way (prevents username enumeration by timing).
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16), method="scrypt")


def _start_session(user: User, remember: bool = False) -> None:
    session.clear()  # drop any pre-login state (session fixation defence)
    login_user(user, remember=remember)
    session.permanent = True


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("workouts.today"))
    form = RegisterForm()
    if form.validate_on_submit():
        username = form.username.data.strip().lower()
        if User.query.filter_by(username=username).first():
            form.username.errors.append("That username is taken.")
        else:
            user = User(
                username=username,
                display_name=(form.display_name.data or form.username.data).strip(),
                timezone=current_app.config["DEFAULT_TIMEZONE"],
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            _start_session(user)
            flash("Welcome! Set up your gym so the AI knows what equipment you have.", "success")
            return redirect(url_for("workouts.gym_profile"))
    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute;50 per hour", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("workouts.today"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data.strip().lower()).first()
        if user and user.check_password(form.password.data):
            _start_session(user, remember=form.remember.data)
            return redirect(safe_next_url(request.args.get("next")) or url_for("workouts.today"))
        if user is None:
            check_password_hash(_DUMMY_HASH, form.password.data)
        log.info("Failed login for username=%r from %s", form.username.data[:32], request.remote_addr)
        # Same message whether the user exists or not.
        flash("Wrong username or password.", "error")
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Google sign-in (OpenID Connect)
# ---------------------------------------------------------------------------
@bp.route("/login/google")
@limiter.limit("20 per minute")
def google_login():
    google = oauth.create_client("google")
    if google is None:
        abort(404)
    # Remember whether this is "link to my existing account" or "sign in".
    session["google_link"] = current_user.is_authenticated
    redirect_uri = url_for("auth.google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)  # Authlib adds state + nonce


@bp.route("/auth/google/callback")
@limiter.limit("20 per minute")
def google_callback():
    google = oauth.create_client("google")
    if google is None:
        abort(404)
    try:
        token = google.authorize_access_token()  # verifies state, nonce, ID token
    except Exception:  # noqa: BLE001 - any OAuth failure is a failed sign-in
        log.warning("Google OAuth callback failed", exc_info=True)
        flash("Google sign-in failed. Please try again.", "error")
        return redirect(url_for("auth.login"))

    info = token.get("userinfo") or {}
    sub = info.get("sub")
    if not sub or not info.get("email_verified"):
        flash("Your Google account email isn't verified.", "error")
        return redirect(url_for("auth.login"))

    linking = session.pop("google_link", False) and current_user.is_authenticated
    existing = User.query.filter_by(google_sub=sub).first()

    if linking:
        if existing and existing.id != current_user.id:
            flash("That Google account is already linked to a different user.", "error")
        else:
            current_user.google_sub = sub
            current_user.email = info.get("email")
            db.session.commit()
            flash("Google account linked.", "success")
        return redirect(url_for("auth.settings"))

    if existing is None:
        # We deliberately do NOT auto-merge with a password account that has the
        # same email: local emails are unverified, so that would allow takeover.
        existing = User(
            username=_unique_username(info.get("email", "")),
            display_name=(info.get("given_name") or info.get("name") or "Athlete")[:64],
            email=info.get("email"),
            google_sub=sub,
            timezone=current_app.config["DEFAULT_TIMEZONE"],
        )
        db.session.add(existing)
        db.session.commit()
        _start_session(existing, remember=True)
        flash("Welcome! Set up your gym so the AI knows what equipment you have.", "success")
        return redirect(url_for("workouts.gym_profile"))

    _start_session(existing, remember=True)
    return redirect(url_for("workouts.today"))


def _unique_username(email: str) -> str:
    base = re.sub(r"[^a-z0-9_.-]", "", email.split("@")[0].lower())[:24] or "athlete"
    if len(base) < 3:
        base = f"{base}fit"
    candidate = base
    while User.query.filter_by(username=candidate).first():
        candidate = f"{base}{secrets.randbelow(9000) + 1000}"
    return candidate


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _settings_context() -> dict:
    uid = current_user.id
    tokens = ApiToken.query.filter_by(user_id=uid).all()
    used = [t.last_used_at for t in tokens if t.last_used_at is not None]
    return {
        "trainer_form": TrainerSettingsForm(
            formdata=None, unit=prefs.unit_for(uid), default_minutes=prefs.default_minutes_for(uid)
        ),
        "gym": GymProfile.query.filter_by(user_id=uid).first(),
        "apple_tokens": len(tokens),
        "apple_last_used": max(used) if used else None,
        "oura": Integration.query.filter_by(user_id=uid, provider="oura").first(),
        "share_links": ShareLink.query.filter_by(user_id=uid, revoked=False)
        .filter(ShareLink.expires_at > utcnow())
        .count(),
    }


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    form = SettingsForm(obj=current_user)
    pw_form = ChangePasswordForm(prefix="pw")
    if form.validate_on_submit():
        current_user.display_name = form.display_name.data.strip()
        current_user.timezone = form.timezone.data.strip()
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("auth.settings"))
    return render_template("auth/settings.html", form=form, pw_form=pw_form, **_settings_context())


@bp.route("/settings/trainer", methods=["POST"])
@login_required
def trainer_settings():
    form = TrainerSettingsForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for err in errors:
                flash(err, "error")
        return redirect(url_for("auth.settings"))
    row = prefs.get_or_create_settings(current_user.id)
    row.unit = form.unit.data
    row.default_minutes = form.default_minutes.data
    db.session.commit()
    flash("Coach settings saved.", "success")
    return redirect(url_for("auth.settings"))


@bp.route("/settings/password", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def change_password():
    form = ChangePasswordForm(prefix="pw")
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for err in errors:
                flash(err, "error")
        return redirect(url_for("auth.settings"))
    if current_user.password_hash and not current_user.check_password(form.current.data or ""):
        flash("Current password is wrong.", "error")
        return redirect(url_for("auth.settings"))
    current_user.set_password(form.password.data)
    current_user.session_version += 1  # signs out every other device
    db.session.commit()
    _start_session(current_user._get_current_object())
    flash("Password updated. Other devices were signed out.", "success")
    return redirect(url_for("auth.settings"))


@bp.route("/settings/logout-everywhere", methods=["POST"])
@login_required
def logout_everywhere():
    current_user.session_version += 1
    db.session.commit()
    logout_user()
    session.clear()
    flash("Signed out on every device.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/settings/delete-account", methods=["POST"])
@login_required
def delete_account():
    if request.form.get("confirm") != current_user.username:
        flash("Type your username exactly to confirm.", "error")
        return redirect(url_for("auth.settings"))
    uid = current_user.id
    for integration in Integration.query.filter_by(user_id=uid, provider="oura"):
        from ..services import oura

        oura.revoke(integration)
    # Bulk deletes bypass ORM cascades, so dependants go first (BUILD_CONTRACT section 1).
    for model in (
        AIUsage,
        DailyPlan,
        SetLog,
        EntryPlan,
        ExerciseMuscles,
        TrainerSettings,
        ShareLink,
        ApiToken,
        HealthSample,
        Integration,
        GymProfile,
    ):
        model.query.filter_by(user_id=uid).delete()
    for ws in WorkoutSession.query.filter_by(user_id=uid):
        db.session.delete(ws)  # cascades to entries
    Exercise.query.filter_by(user_id=uid).delete()
    db.session.delete(db.session.get(User, uid))
    db.session.commit()
    logout_user()
    session.clear()
    flash("Your account and all your data were deleted.", "success")
    return redirect(url_for("auth.login"))
