"""Share a day's summary with a friend via an unguessable, expiring, revocable link."""

from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, make_response, redirect, render_template, url_for
from flask_login import current_user, login_required

from ..extensions import db, limiter
from ..forms import ShareForm
from ..models import ShareLink, utcnow
from ..security import hash_token, new_token, owned_or_404
from ..services import calories
from ..timeutil import today_for

bp = Blueprint("sharing", __name__)


@bp.route("/share", methods=["POST"])
@login_required
@limiter.limit("30 per hour")
def create():
    form = ShareForm()
    if not form.validate_on_submit():
        flash("Pick a valid day to share.", "error")
        return redirect(url_for("workouts.today"))
    day = form.day.data
    if day > today_for(current_user.timezone):
        flash("You can't share a day in the future.", "error")
        return redirect(url_for("workouts.today"))

    raw = new_token()
    link = ShareLink(
        user_id=current_user.id,
        day=day,
        token_hash=hash_token(raw),
        expires_at=utcnow() + timedelta(days=current_app.config["SHARE_LINK_DAYS"]),
    )
    db.session.add(link)
    db.session.commit()
    url = url_for("sharing.public", token=raw, _external=True)
    summary = calories.day_summary(current_user, day)
    return render_template("sharing/created.html", url=url, link=link, summary=summary)


@bp.route("/shares")
@login_required
def manage():
    links = (
        ShareLink.query.filter_by(user_id=current_user.id, revoked=False)
        .filter(ShareLink.expires_at > utcnow())
        .order_by(ShareLink.created_at.desc())
        .all()
    )
    return render_template("sharing/manage.html", links=links)


@bp.route("/shares/<int:link_id>/revoke", methods=["POST"])
@login_required
def revoke(link_id: int):
    link = owned_or_404(ShareLink, link_id)
    link.revoked = True
    db.session.commit()
    flash("Link revoked. Anyone who has it can no longer open it.", "success")
    return redirect(url_for("sharing.manage"))


@bp.route("/s/<token>")
@limiter.limit("60 per minute")
def public(token: str):
    if len(token) > 64:
        abort(404)
    link = ShareLink.query.filter_by(token_hash=hash_token(token)).first()
    if link is None or link.revoked or link.expires_at <= utcnow():
        abort(404)
    summary = calories.day_summary(link.user, link.day)
    # Only the minimum is exposed: first name, the day, exercises and calories.
    resp = make_response(render_template("sharing/public.html", owner=link.user, summary=summary))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "no-referrer"  # don't leak the token to other sites
    resp.headers["Cache-Control"] = "private, max-age=60"
    return resp
