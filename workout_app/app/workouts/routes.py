"""Today view, gym profile, exercise library, AI builder, workout sessions."""

from __future__ import annotations

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from pydantic import ValidationError

from ..extensions import db, limiter
from ..forms import (
    AIGenerateForm,
    EntryForm,
    ExerciseForm,
    GymProfileForm,
    ManualCaloriesForm,
)
from ..models import Exercise, GymProfile, Integration, SessionEntry, WorkoutSession, utcnow
from ..security import owned_or_404
from ..services import ai_coach, calories, oura, youtube
from ..timeutil import today_for

bp = Blueprint("workouts", __name__)
log = logging.getLogger(__name__)


def _gym() -> GymProfile:
    gym = GymProfile.query.filter_by(user_id=current_user.id).first()
    if gym is None:
        gym = GymProfile(user_id=current_user.id)
        db.session.add(gym)
        db.session.commit()
    return gym


# ---------------------------------------------------------------------------
# Today & history
# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
def today():
    day = today_for(current_user.timezone)
    summary = calories.day_summary(current_user, day)
    active = next((s for s in summary.sessions if s.is_active), None)
    return render_template("workouts/today.html", summary=summary, active=active)


@bp.route("/history")
@login_required
def history():
    day = today_for(current_user.timezone)
    days = calories.recent_days(current_user, day, count=14)
    return render_template("workouts/history.html", days=days)


# ---------------------------------------------------------------------------
# Gym profile (drives the AI builder)
# ---------------------------------------------------------------------------
@bp.route("/gym", methods=["GET", "POST"])
@login_required
def gym_profile():
    gym = _gym()
    form = GymProfileForm(obj=gym)
    if request.method == "GET":
        form.equipment.data = gym.equipment
    if form.validate_on_submit():
        gym.gym_size = form.gym_size.data
        gym.equipment = form.equipment.data or []
        gym.goal = form.goal.data
        gym.experience = form.experience.data
        gym.notes = (form.notes.data or "").strip()
        db.session.commit()
        flash("Gym profile saved.", "success")
        return redirect(url_for("workouts.exercises"))
    return render_template("workouts/gym.html", form=form)


# ---------------------------------------------------------------------------
# Exercise library
# ---------------------------------------------------------------------------
@bp.route("/exercises")
@login_required
def exercises():
    items = (
        Exercise.query.filter_by(user_id=current_user.id).order_by(Exercise.muscle_group, Exercise.name).all()
    )
    return render_template("workouts/exercises.html", exercises=items, search_url=youtube.search_url)


def _apply_exercise_form(ex: Exercise, form: ExerciseForm) -> None:
    ex.name = form.name.data.strip()
    ex.muscle_group = (form.muscle_group.data or "").strip()
    ex.equipment = (form.equipment.data or "").strip()
    ex.sets = form.sets.data
    ex.reps = form.reps.data.strip()
    ex.rest_seconds = form.rest_seconds.data or 0
    ex.instructions = (form.instructions.data or "").strip()
    ex.youtube_video_id = youtube.parse_video_id(form.youtube_url.data) if form.youtube_url.data else None


@bp.route("/exercises/new", methods=["GET", "POST"])
@login_required
def exercise_new():
    form = ExerciseForm()
    if form.validate_on_submit():
        ex = Exercise(user_id=current_user.id, source="manual")
        _apply_exercise_form(ex, form)
        if not ex.youtube_video_id:
            ex.youtube_video_id = youtube.find_video_id(ex.name)
        db.session.add(ex)
        db.session.commit()
        flash(f"Added {ex.name}.", "success")
        return redirect(url_for("workouts.exercises"))
    return render_template("workouts/exercise_form.html", form=form, title="Add exercise")


@bp.route("/exercises/<int:exercise_id>/edit", methods=["GET", "POST"])
@login_required
def exercise_edit(exercise_id: int):
    ex = owned_or_404(Exercise, exercise_id)
    form = ExerciseForm(obj=ex)
    if request.method == "GET" and ex.youtube_video_id:
        form.youtube_url.data = f"https://www.youtube.com/watch?v={ex.youtube_video_id}"
    if form.validate_on_submit():
        _apply_exercise_form(ex, form)
        db.session.commit()
        flash("Exercise updated.", "success")
        return redirect(url_for("workouts.exercises"))
    return render_template("workouts/exercise_form.html", form=form, title=f"Edit {ex.name}", exercise=ex)


@bp.route("/exercises/<int:exercise_id>/delete", methods=["POST"])
@login_required
def exercise_delete(exercise_id: int):
    ex = owned_or_404(Exercise, exercise_id)
    db.session.delete(ex)
    db.session.commit()
    flash("Exercise deleted.", "success")
    return redirect(url_for("workouts.exercises"))


# ---------------------------------------------------------------------------
# AI builder
# ---------------------------------------------------------------------------
@bp.route("/exercises/ai", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per hour", methods=["POST"])  # protects the Anthropic bill
def exercise_ai():
    gym = _gym()
    form = AIGenerateForm()
    if form.validate_on_submit():
        focus_label = dict(form.focus.choices).get(form.focus.data, form.focus.data)
        try:
            plan = ai_coach.generate_workout(
                gym_size=gym.gym_size,
                equipment=gym.equipment,
                goal=gym.goal,
                experience=gym.experience,
                notes=gym.notes,
                focus=form.focus.data,
                count=form.count.data,
                minutes=form.minutes.data,
                extra=form.extra.data,
            )
        except ai_coach.AIError as exc:
            flash(str(exc), "error")
        else:
            return render_template("workouts/ai_preview.html", plan=plan, focus=focus_label)
    return render_template("workouts/ai_form.html", form=form, gym=gym)


@bp.route("/exercises/ai/save", methods=["POST"])
@login_required
def exercise_ai_save():
    fields = ["name", "muscle_group", "equipment", "sets", "reps", "rest_seconds", "instructions"]
    columns = {f: request.form.getlist(f) for f in fields}
    selected = set(request.form.getlist("selected"))
    rows = len(columns["name"])
    if rows > 12 or any(len(v) != rows for v in columns.values()):
        flash("That form looked wrong. Please generate again.", "error")
        return redirect(url_for("workouts.exercise_ai"))

    saved = 0
    for i in range(rows):
        if str(i) not in selected:
            continue
        try:
            # Re-validate: hidden fields are user-controlled, just like a manual add.
            item = ai_coach.GeneratedExercise(**{f: columns[f][i] for f in fields})
        except ValidationError:
            continue
        db.session.add(
            Exercise(
                user_id=current_user.id,
                source="ai",
                youtube_video_id=youtube.find_video_id(item.name),
                **item.model_dump(),
            )
        )
        saved += 1
    db.session.commit()
    flash(f"Saved {saved} exercise{'s' if saved != 1 else ''} to your library.", "success")
    return redirect(url_for("workouts.exercises"))


# ---------------------------------------------------------------------------
# Workout sessions
# ---------------------------------------------------------------------------
@bp.route("/sessions/start", methods=["POST"])
@login_required
def session_start():
    open_session = WorkoutSession.query.filter_by(user_id=current_user.id, ended_at=None).first()
    if open_session:
        return redirect(url_for("workouts.session_view", session_id=open_session.id))
    ws = WorkoutSession(user_id=current_user.id, day=today_for(current_user.timezone))
    db.session.add(ws)
    db.session.commit()
    return redirect(url_for("workouts.session_view", session_id=ws.id))


def _entry_form() -> EntryForm:
    form = EntryForm()
    library = Exercise.query.filter_by(user_id=current_user.id).order_by(Exercise.name).all()
    form.exercise_id.choices = [(e.id, e.name) for e in library]
    return form


@bp.route("/sessions/<int:session_id>", methods=["GET", "POST"])
@login_required
def session_view(session_id: int):
    ws = owned_or_404(WorkoutSession, session_id)
    form = _entry_form()
    if form.validate_on_submit():
        ex = owned_or_404(Exercise, form.exercise_id.data)
        db.session.add(
            SessionEntry(
                session_id=ws.id,
                exercise_id=ex.id,
                exercise_name=ex.name,
                sets_done=form.sets_done.data,
                reps_done=(form.reps_done.data or ex.reps).strip(),
                weight=(form.weight.data or "").strip(),
            )
        )
        db.session.commit()
        return redirect(url_for("workouts.session_view", session_id=ws.id))
    library = {e.id: e for e in Exercise.query.filter_by(user_id=current_user.id).all()}
    return render_template(
        "workouts/session.html",
        ws=ws,
        form=form,
        library=library,
        cal_form=ManualCaloriesForm(),
        apple_shortcut_url=_apple_shortcut_url(ws),
    )


@bp.route("/sessions/<int:session_id>/entries/<int:entry_id>/delete", methods=["POST"])
@login_required
def entry_delete(session_id: int, entry_id: int):
    ws = owned_or_404(WorkoutSession, session_id)
    entry = db.session.get(SessionEntry, entry_id)
    if entry is None or entry.session_id != ws.id:
        return redirect(url_for("workouts.session_view", session_id=ws.id))
    db.session.delete(entry)
    db.session.commit()
    return redirect(url_for("workouts.session_view", session_id=ws.id))


def _sync_and_attribute(ws: WorkoutSession) -> None:
    integration = Integration.query.filter_by(user_id=current_user.id, provider="oura").first()
    if integration:
        try:
            oura.sync(integration, days=2)
        except oura.OuraError as exc:
            flash(f"Oura: {exc}", "error")
    if ws.calories_source == "manual":
        return
    kcal, source = calories.session_calories(ws)
    if kcal is not None:
        ws.calories, ws.calories_source = kcal, source


@bp.route("/sessions/<int:session_id>/finish", methods=["POST"])
@login_required
def session_finish(session_id: int):
    ws = owned_or_404(WorkoutSession, session_id)
    if ws.ended_at is None:
        ws.ended_at = utcnow()
    _sync_and_attribute(ws)
    db.session.commit()
    if ws.calories is None:
        flash(
            "Workout saved! No wearable calories yet — they usually arrive a few minutes after "
            "your watch/ring syncs. Tap “Refresh calories” later or enter them manually.",
            "info",
        )
    else:
        flash(f"Great work! You burned {ws.calories} active calories this session.", "success")
    return redirect(url_for("workouts.session_view", session_id=ws.id))


@bp.route("/sessions/<int:session_id>/refresh", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def session_refresh(session_id: int):
    ws = owned_or_404(WorkoutSession, session_id)
    _sync_and_attribute(ws)
    db.session.commit()
    return redirect(url_for("workouts.session_view", session_id=ws.id))


@bp.route("/sessions/<int:session_id>/calories", methods=["POST"])
@login_required
def session_manual_calories(session_id: int):
    ws = owned_or_404(WorkoutSession, session_id)
    form = ManualCaloriesForm()
    if form.validate_on_submit():
        ws.calories, ws.calories_source = form.calories.data, "manual"
        db.session.commit()
    return redirect(url_for("workouts.session_view", session_id=ws.id))


@bp.route("/sessions/<int:session_id>/delete", methods=["POST"])
@login_required
def session_delete(session_id: int):
    ws = owned_or_404(WorkoutSession, session_id)
    db.session.delete(ws)
    db.session.commit()
    flash("Workout deleted.", "success")
    return redirect(url_for("workouts.today"))


def _apple_shortcut_url(ws: WorkoutSession) -> str | None:
    """Deep link that runs the iPhone Shortcut with this session's time window."""
    if ws.ended_at is None:
        return None
    from urllib.parse import quote

    start = ws.started_at.replace(microsecond=0).isoformat() + "Z"
    end = ws.ended_at.replace(microsecond=0).isoformat() + "Z"
    window = f"{ws.id}|{start}|{end}"
    return "shortcuts://run-shortcut?name=" + quote("Workout Calories") + "&input=text&text=" + quote(window)
