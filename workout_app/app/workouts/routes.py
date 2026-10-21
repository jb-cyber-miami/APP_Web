"""Today, history, progress, gym profile, exercise library, AI builder, sessions and set logging.

Routes stay thin: parse the request, check ownership (``owned_or_404`` plus child-row checks),
call a service, commit, render. Services never commit. See docs/BUILD_CONTRACT.md section 4.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from pydantic import ValidationError
from sqlalchemy import select

from ..extensions import db, limiter, user_or_ip
from ..forms import (
    AIGenerateForm,
    EntryForm,
    ExerciseForm,
    GymProfileForm,
    ManualCaloriesForm,
    PlanGenerateForm,
    RateForm,
    SetLogForm,
    SwapForm,
)
from ..models import (
    DailyPlan,
    Exercise,
    GymProfile,
    Integration,
    SessionEntry,
    SetLog,
    WorkoutSession,
    utcnow,
)
from ..security import owned_or_404
from ..services import (
    activity,
    ai_coach,
    calories,
    oura,
    prefs,
    progression,
    recovery,
    sets,
    strength,
    trainer,
    youtube,
)
from ..services import (
    summary as summary_svc,
)
from ..services.muscles import CANONICAL, LABELS, MuscleMap, ensure_muscles, library_maps, muscles_for
from ..services.plan_schema import PlanRowView, TodayPlan, load_exercises, load_plan, plan_row_view
from ..timeutil import to_local, today_for

bp = Blueprint("workouts", __name__)
log = logging.getLogger(__name__)

HERO_ROWS = 3
HISTORY_SESSIONS = 10
SPARK_POINTS = 12


def _gym() -> GymProfile:
    gym = GymProfile.query.filter_by(user_id=current_user.id).first()
    if gym is None:
        gym = GymProfile(user_id=current_user.id)
        db.session.add(gym)
        db.session.commit()
    return gym


def _open_session() -> WorkoutSession | None:
    return (
        WorkoutSession.query.filter_by(user_id=current_user.id, ended_at=None)
        .order_by(WorkoutSession.started_at.desc())
        .first()
    )


def _linked_plan(ws: WorkoutSession) -> tuple[DailyPlan | None, TodayPlan | None]:
    row = DailyPlan.query.filter_by(user_id=ws.user_id, session_id=ws.id).first()
    return row, (load_plan(row.plan_json) if row is not None else None)


# ---------------------------------------------------------------------------
# View shapes (BUILD_CONTRACT 4.2)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HeroView:
    state: Literal["plan", "active", "done", "rest"]
    title: str
    plan: TodayPlan | None
    plan_saved: bool
    session: WorkoutSession | None
    rows: list[PlanRowView]
    more: int
    total_sets: int
    focus: list[recovery.RecoveryView]
    exercises_done: int = 0
    exercises_total: int = 0
    done_kcal: int | None = None
    done_minutes: int | None = None


@dataclass(frozen=True)
class HistoryRow:
    day: date
    session_id: int
    sets: list[str]
    e1rm: float | None


def _hero(day: date, day_summary, plan: TodayPlan, row: DailyPlan | None, panel) -> HeroView:
    focus = [panel.by_muscle[m] for m in plan.focus_muscles if m in panel.by_muscle]
    open_ws = _open_session()
    if open_ws is not None:
        _, linked = _linked_plan(open_ws)
        done, total = sets.progress(open_ws)
        return HeroView(
            state="active",
            title=(linked.title if linked else "Workout"),
            plan=linked,
            plan_saved=linked is not None,
            session=open_ws,
            rows=[],
            more=0,
            total_sets=sum(len(ex.sets) for ex in linked.exercises) if linked else 0,
            focus=[panel.by_muscle[m] for m in linked.focus_muscles if m in panel.by_muscle]
            if linked
            else [],
            exercises_done=done,
            exercises_total=total,
        )
    finished = [s for s in day_summary.sessions if s.ended_at is not None]
    if finished and (row is None or row.session_id is not None):
        last = max(finished, key=lambda s: s.ended_at)
        minutes = max(0, int((last.ended_at - last.started_at).total_seconds() // 60))
        linked_title = plan.title if row is not None and row.session_id == last.id else "Workout done"
        return HeroView(
            state="done",
            title=linked_title,
            plan=plan if row is not None else None,
            plan_saved=row is not None,
            session=last,
            rows=[],
            more=0,
            total_sets=0,
            focus=focus,
            done_kcal=last.calories,
            done_minutes=minutes,
        )
    rows = [plan_row_view(ex) for ex in plan.exercises]
    return HeroView(
        state="rest" if plan.kind == "recovery" else "plan",
        title=plan.title,
        plan=plan,
        plan_saved=row is not None,
        session=None,
        rows=rows[:HERO_ROWS],
        more=max(0, len(rows) - HERO_ROWS),
        total_sets=sum(len(ex.sets) for ex in plan.exercises),
        focus=focus,
    )


def _ai_left(row: DailyPlan | None) -> int | None:
    if not current_app.config.get("ANTHROPIC_API_KEY"):
        return None
    gens_left = current_app.config["AI_MAX_PLAN_GENERATIONS"] - (row.generations if row else 0)
    return max(0, min(gens_left, ai_coach.remaining_calls(current_user.id)))


# ---------------------------------------------------------------------------
# Today, history, progress
# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
def today():
    user = current_user._get_current_object()
    now = utcnow()
    day = today_for(user.timezone)
    day_summary = calories.day_summary(user, day)
    active = next((s for s in day_summary.sessions if s.is_active), None)
    plan, row = trainer.today_plan(user, now=now)
    panel = recovery.recovery_panel(recovery.recovery_map(user.id, now), now)
    return render_template(
        "workouts/today.html",
        summary=day_summary,
        active=active,
        hero=_hero(day, day_summary, plan, row, panel),
        recovery=panel,
        week=activity.week_strip(user, day),
        streak=activity.streak_weeks(user, day),
        plan_form=PlanGenerateForm(minutes=prefs.default_minutes_for(user.id)),
        ai_left=_ai_left(row),
        unit=prefs.unit_for(user.id),
    )


def _parse_day(raw: str | None, today: date) -> date | None:
    if not raw or len(raw) != 10:
        return None
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return None
    return day if day <= today else None


@bp.route("/history")
@login_required
def history():
    today = today_for(current_user.timezone)
    single = _parse_day(request.args.get("day"), today)
    if single is not None:
        days = [calories.day_summary(current_user, single)]
    else:
        days = calories.recent_days(current_user, today, count=14)
    return render_template("workouts/history.html", days=days, single_day=single is not None)


def _parse_month(raw: str | None, today: date) -> tuple[int, int]:
    if raw and len(raw) == 7 and raw[4] == "-":
        try:
            year, month = int(raw[:4]), int(raw[5:])
        except ValueError:
            year = month = 0
        if 2000 <= year <= 2100 and 1 <= month <= 12 and (year, month) <= (today.year, today.month):
            return year, month
    return today.year, today.month


@bp.route("/progress")
@login_required
def progress():
    user = current_user._get_current_object()
    today = today_for(user.timezone)
    unit = prefs.unit_for(user.id)
    report = strength.strength_report(user.id, now=utcnow(), unit=unit)
    year, month = _parse_month(request.args.get("month"), today)
    top = sorted(report.lifts, key=lambda lf: (-lf.current_e1rm_lb, lf.name.lower()))[:5]
    return render_template(
        "workouts/progress.html",
        report=report,
        rows=strength.strength_rows(report),
        overall_pct=strength.index_pct(report.overall),
        top_lifts=top,
        month=activity.month_calendar(user, year, month, today),
        streak=activity.streak_weeks(user, today),
        unit=unit,
    )


# ---------------------------------------------------------------------------
# Gym profile (drives the AI builder and the coach)
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
    muscle_of: dict[int, MuscleMap] = library_maps(current_user.id)
    muscle_filter = request.args.get("muscle")
    if muscle_filter not in CANONICAL:
        muscle_filter = None
    by_group: dict[str, list[Exercise]] = {}
    for ex in sorted(items, key=lambda e: e.name.lower()):
        mm = muscle_of.get(ex.id)
        key = mm.primary if mm and mm.primary else "other"
        by_group.setdefault(key, []).append(ex)
    groups = [(m, LABELS[m], by_group[m]) for m in CANONICAL if m in by_group]
    if "other" in by_group:
        groups.append(("other", "Other", by_group["other"]))
    if muscle_filter:
        groups = [g for g in groups if g[0] == muscle_filter]
        items = [ex for ex in items if (muscle_of.get(ex.id) or MuscleMap(None)).primary == muscle_filter]
    return render_template(
        "workouts/exercises.html",
        exercises=items,
        search_url=youtube.search_url,
        groups=groups,
        muscle_filter=muscle_filter,
        muscle_of=muscle_of,
    )


def _set_text(log_row: SetLog) -> str:
    if log_row.seconds is not None and log_row.reps is None:
        secs = f"{log_row.seconds}s"
        return f"{sets.num_text(log_row.weight)} × {secs}" if log_row.weight is not None else secs
    weight = sets.num_text(log_row.weight) if log_row.weight is not None else "BW"
    return f"{weight} × {log_row.reps if log_row.reps is not None else 0}"


def _history_rows(ex: Exercise, exposures, unit: str) -> list[HistoryRow]:
    e1rm_by_session = {}
    for exp in exposures:
        v = progression.exposure_e1rm(exp)
        e1rm_by_session[exp.session_id] = round(progression.from_lb(v, unit), 1) if v is not None else None
    entries = (
        SessionEntry.query.join(WorkoutSession, SessionEntry.session_id == WorkoutSession.id)
        .filter(WorkoutSession.user_id == ex.user_id, SessionEntry.exercise_id == ex.id)
        .order_by(WorkoutSession.started_at.desc(), SessionEntry.id)
        .limit(HISTORY_SESSIONS * 3)
        .all()
    )
    rows: dict[int, HistoryRow] = {}
    for entry in entries:
        if entry.set_logs:
            texts = [_set_text(s) for s in entry.set_logs]
        elif entry.plan is not None:
            continue  # tracked but nothing logged
        else:
            text = f"{entry.sets_done} × {entry.reps_done}" if entry.reps_done else f"{entry.sets_done} sets"
            texts = [f"{text} @ {entry.weight}" if entry.weight else text]
        ws = entry.session
        if ws.id in rows:
            rows[ws.id].sets.extend(texts)
        elif len(rows) < HISTORY_SESSIONS:
            rows[ws.id] = HistoryRow(
                day=ws.day, session_id=ws.id, sets=texts, e1rm=e1rm_by_session.get(ws.id)
            )
    return list(rows.values())


@bp.route("/exercises/<int:exercise_id>")
@login_required
def exercise_detail(exercise_id: int):
    ex = owned_or_404(Exercise, exercise_id)
    unit = prefs.unit_for(current_user.id)
    now = utcnow()
    exposures = progression.load_history(
        current_user.id, progression.exercise_key(ex.id, ex.name), now=now, days=3650, limit=50
    )
    e1rm = best_set = last_day = None
    values: list[float] = []
    best_val = None
    for exp in reversed(exposures):  # oldest -> newest
        v = progression.exposure_e1rm(exp)
        if v is None:
            continue
        values.append(round(progression.from_lb(v, unit), 1))
        for s in exp.sets:
            if s.weight_lb and s.reps:
                sv = progression.epley(s.weight_lb, s.reps)
                if sv is not None and (best_val is None or sv > best_val):
                    best_val = sv
                    w = round(progression.from_lb(s.weight_lb, unit), 1)
                    best_set = f"{progression.fmt_weight(w, unit)} × {s.reps}"
    if exposures:
        latest = exposures[0]
        local = to_local(latest.performed_at, current_user.timezone)
        last_day = local.date() if local else None
        v = progression.exposure_e1rm(latest)
        e1rm = round(progression.from_lb(v, unit), 1) if v is not None else None
    muscles = muscles_for(ex)
    return render_template(
        "workouts/exercise_detail.html",
        exercise=ex,
        muscles=muscles,
        targets=muscles.all,
        e1rm=e1rm,
        best_set=best_set,
        last_day=last_day,
        spark_points=strength.sparkline_points(values[-SPARK_POINTS:]),
        history_rows=_history_rows(ex, exposures, unit),
        search_url=youtube.search_url,
        unit=unit,
    )


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
        ensure_muscles(ex)
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
        ensure_muscles(ex)
        db.session.commit()
        flash("Exercise updated.", "success")
        return redirect(url_for("workouts.exercise_detail", exercise_id=ex.id))
    return render_template("workouts/exercise_form.html", form=form, title=f"Edit {ex.name}", exercise=ex)


@bp.route("/exercises/<int:exercise_id>/delete", methods=["POST"])
@login_required
def exercise_delete(exercise_id: int):
    ex = owned_or_404(Exercise, exercise_id)
    # SQLite can reuse rowids: never leave stale ids that would corrupt progression history.
    own_sessions = select(WorkoutSession.id).where(WorkoutSession.user_id == current_user.id)
    SessionEntry.query.filter(
        SessionEntry.exercise_id == ex.id, SessionEntry.session_id.in_(own_sessions)
    ).update({SessionEntry.exercise_id: None}, synchronize_session="fetch")
    db.session.delete(ex)  # cascades to its ExerciseMuscles row
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
                user_id=current_user.id,
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
            db.session.commit()  # keep any usage accounting
            flash(str(exc), "error")
        else:
            db.session.commit()
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
        ex = Exercise(
            user_id=current_user.id,
            source="ai",
            youtube_video_id=youtube.find_video_id(item.name),
            **item.model_dump(),
        )
        db.session.add(ex)
        ensure_muscles(ex)  # rules: the free-text label is user-controlled here
        saved += 1
    db.session.commit()
    flash(f"Saved {saved} exercise{'s' if saved != 1 else ''} to your library.", "success")
    return redirect(url_for("workouts.exercises"))


# ---------------------------------------------------------------------------
# Today's plan (AI coach or deterministic fallback)
# ---------------------------------------------------------------------------
def _plan_from_form(use_ai: bool):
    form = PlanGenerateForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for err in errors:
                flash(err, "error")
        return redirect(url_for("workouts.today"))
    plan, _row = trainer.generate_plan(
        current_user._get_current_object(),
        minutes=form.minutes.data,
        focus=form.focus.data,
        extra=(form.extra.data or "").strip(),
        use_ai=use_ai,
        now=utcnow(),
    )
    db.session.commit()
    if plan.notice:
        flash(plan.notice, "info")
    else:
        flash("Today's plan is ready.", "success")
    return redirect(url_for("workouts.today"))


@bp.route("/plan/generate", methods=["POST"])
@login_required
@limiter.limit("6 per hour;20 per day", key_func=user_or_ip)
def plan_generate():
    return _plan_from_form(use_ai=True)


@bp.route("/plan/rebuild", methods=["POST"])
@login_required
@limiter.limit("60 per hour", key_func=user_or_ip)
def plan_rebuild():
    return _plan_from_form(use_ai=False)


@bp.route("/plan/start", methods=["POST"])
@login_required
def plan_start():
    ws = trainer.start_plan(current_user._get_current_object(), now=utcnow())
    db.session.commit()
    return redirect(url_for("workouts.session_view", session_id=ws.id))


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


def _log_all(entry: SessionEntry, reps_text: str, weight_text: str, unit: str) -> None:
    """ "Already done": log every target set, preferring what the user typed."""
    typed_reps = progression.parse_reps(reps_text) if reps_text else None
    typed_secs = progression.parse_seconds(reps_text) if reps_text and typed_reps is None else None
    typed_weight = None
    if weight_text:
        parsed = progression.parse_weight(weight_text, unit)
        if parsed.value is not None:
            typed_weight = round(progression.from_lb(progression.to_lb(parsed.value, parsed.unit), unit), 2)
    for i, t in enumerate(sets.entry_targets(entry)):
        timed = t.target_seconds is not None
        reps = None if timed else (typed_reps if typed_reps is not None else t.target_reps)
        secs = (typed_secs or t.target_seconds) if timed else None
        weight = typed_weight if typed_weight is not None else t.weight
        sets.log_set(entry, set_index=i, reps=reps, seconds=secs, weight=weight, rating=None, unit=unit)


@bp.route("/sessions/<int:session_id>", methods=["GET", "POST"])
@login_required
def session_view(session_id: int):
    ws = owned_or_404(WorkoutSession, session_id)
    form = _entry_form()
    if form.validate_on_submit():
        ex = owned_or_404(Exercise, form.exercise_id.data)
        reps_text = (form.reps_done.data or "").strip()
        weight_text = (form.weight.data or "").strip()
        entry = SessionEntry(
            session_id=ws.id,
            exercise_id=ex.id,
            exercise_name=ex.name,
            sets_done=form.sets_done.data,
            reps_done=(reps_text or ex.reps).strip()[:20],
            weight=weight_text[:20],
        )
        db.session.add(entry)
        db.session.flush()
        if ex.muscles_row is None:
            ensure_muscles(ex)
        trainer.attach_targets(
            current_user._get_current_object(),
            entry,
            ex,
            sets=min(form.sets_done.data, sets.MAX_SETS),
            reps_text=reps_text,
            weight_text=weight_text,
            now=utcnow(),
        )
        # A finished workout is being back-filled: everything typed was already done.
        if form.log_all.data or ws.ended_at is not None:
            _log_all(entry, reps_text, weight_text, prefs.unit_for(current_user.id))
        sets.sync_entry(entry)
        db.session.commit()
        return redirect(url_for("workouts.session_view", session_id=ws.id) + f"#ex-{entry.id}")
    library = {e.id: e for e in Exercise.query.filter_by(user_id=current_user.id).all()}
    unit = prefs.unit_for(current_user.id)
    _row, linked = _linked_plan(ws)
    return render_template(
        "workouts/session.html",
        ws=ws,
        form=form,
        library=library,
        cal_form=ManualCaloriesForm(),
        apple_shortcut_url=_apple_shortcut_url(ws),
        entries=sets.entry_views(ws, library=library),
        plan_title=linked.title if linked else None,
        progress=sets.progress(ws),
        unlogged=sets.unlogged_sets(ws),
        unit=unit,
        summary=summary_svc.workout_summary(ws, unit=unit) if ws.ended_at is not None else None,
        focus_mode=ws.is_active,
    )


def _session_entry_or_404(session_id: int, entry_id: int) -> tuple[WorkoutSession, SessionEntry]:
    ws = owned_or_404(WorkoutSession, session_id)
    entry = db.session.get(SessionEntry, entry_id)
    if entry is None or entry.session_id != ws.id:
        abort(404)
    return ws, entry


def _session_set_or_404(session_id: int, set_id: int) -> tuple[WorkoutSession, SetLog]:
    ws = owned_or_404(WorkoutSession, session_id)
    log_row = owned_or_404(SetLog, set_id)
    if log_row.entry is None or log_row.entry.session_id != ws.id:
        abort(404)
    return ws, log_row


@bp.route("/sessions/<int:session_id>/entries/<int:entry_id>/delete", methods=["POST"])
@login_required
def entry_delete(session_id: int, entry_id: int):
    ws = owned_or_404(WorkoutSession, session_id)
    entry = db.session.get(SessionEntry, entry_id)
    if entry is None or entry.session_id != ws.id:
        return redirect(url_for("workouts.session_view", session_id=ws.id))
    db.session.delete(entry)  # cascades to its SetLogs and EntryPlan
    db.session.commit()
    return redirect(url_for("workouts.session_view", session_id=ws.id))


# ---------------------------------------------------------------------------
# Set logging (JSON for fetch, PRG otherwise)
# ---------------------------------------------------------------------------
def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "fetch"


def _back(ws: WorkoutSession, entry_id: int | None = None):
    url = url_for("workouts.session_view", session_id=ws.id)
    return redirect(url + (f"#ex-{entry_id}" if entry_id else ""))


def _set_error(ws: WorkoutSession, entry_id: int | None, message: str):
    if _wants_json():
        return jsonify({"ok": False, "error": message}), 400
    flash(message, "error")
    return _back(ws, entry_id)


def _first_form_error(form) -> str:
    for field, errors in form.errors.items():
        if errors:
            label = getattr(getattr(form, field, None), "label", None)
            name = label.text if label is not None else field
            return f"{name}: {errors[0]}"
    return "That set looked wrong. Please try again."


def _set_payload(ws: WorkoutSession, entry: SessionEntry, set_index: int, log_row: SetLog | None):
    done, total = sets.progress(ws)
    if log_row is not None:
        action = url_for("workouts.set_delete", session_id=ws.id, set_id=log_row.id)
        rate_action = url_for("workouts.set_rate", session_id=ws.id, set_id=log_row.id)
    else:
        action = url_for("workouts.set_log", session_id=ws.id, entry_id=entry.id)
        rate_action = None
    return jsonify(
        {
            "ok": True,
            "set_id": log_row.id if log_row is not None else None,
            "set_index": set_index,
            "is_done": log_row is not None,
            "rating": log_row.rating if log_row is not None else None,
            "entry_done": len(entry.set_logs),
            "entry_planned": sets.planned_count(entry),
            "exercises_done": done,
            "exercises_total": total,
            "unlogged_sets": sets.unlogged_sets(ws),
            "rest_seconds": entry.plan.rest_seconds if entry.plan is not None else 0,
            "action": action,
            "rate_action": rate_action,
        }
    )


@bp.route("/sessions/<int:session_id>/entries/<int:entry_id>/sets", methods=["POST"])
@login_required
@limiter.limit("600 per hour", key_func=user_or_ip)
def set_log(session_id: int, entry_id: int):
    ws, entry = _session_entry_or_404(session_id, entry_id)
    if ws.ended_at is not None:
        return _set_error(ws, entry.id, "This workout is finished.")
    if entry.plan is None:
        return _set_error(ws, entry.id, "Sets can't be checked off for this exercise.")
    form = SetLogForm()
    if not form.validate_on_submit():
        return _set_error(ws, entry.id, _first_form_error(form))
    set_index = form.set_index.data
    targets = sets.entry_targets(entry)
    if set_index >= sets.planned_count(entry, targets):
        return _set_error(ws, entry.id, "That set doesn't exist.")
    target = targets[set_index] if set_index < len(targets) else (targets[-1] if targets else None)
    timed = target is not None and target.target_seconds is not None
    reps, seconds = form.reps.data, form.seconds.data
    if timed:
        reps = None
        seconds = seconds if seconds is not None else target.target_seconds
    else:
        seconds = None if reps is not None or target is not None else seconds
        if reps is None and seconds is None and target is not None:
            reps = target.target_reps
    if reps is None and seconds is None:
        return _set_error(ws, entry.id, "Enter reps for this set.")
    weight = float(form.weight.data) if form.weight.data is not None else None
    log_row = sets.log_set(
        entry,
        set_index=set_index,
        reps=reps,
        seconds=seconds,
        weight=weight,
        rating=form.rating.data or None,
        unit=prefs.unit_for(current_user.id),
    )
    db.session.commit()
    if _wants_json():
        return _set_payload(ws, entry, set_index, log_row)
    return _back(ws, entry.id)


@bp.route("/sessions/<int:session_id>/entries/<int:entry_id>/sets/add", methods=["POST"])
@login_required
def set_add(session_id: int, entry_id: int):
    ws, entry = _session_entry_or_404(session_id, entry_id)
    if ws.ended_at is not None:
        return _set_error(ws, entry.id, "This workout is finished.")
    if entry.plan is None:
        return _set_error(ws, entry.id, "Sets can't be added to this exercise.")
    if sets.planned_count(entry) >= sets.MAX_SETS:
        return _set_error(ws, entry.id, f"An exercise can have at most {sets.MAX_SETS} sets.")
    sets.add_set(entry)
    db.session.commit()
    return _back(ws, entry.id)


@bp.route("/sessions/<int:session_id>/sets/<int:set_id>/delete", methods=["POST"])
@login_required
def set_delete(session_id: int, set_id: int):
    ws, log_row = _session_set_or_404(session_id, set_id)
    entry = log_row.entry
    if ws.ended_at is not None:
        return _set_error(ws, entry.id, "This workout is finished.")
    set_index = log_row.set_index
    sets.delete_set(log_row)
    db.session.commit()
    if _wants_json():
        return _set_payload(ws, entry, set_index, None)
    return _back(ws, entry.id)


@bp.route("/sessions/<int:session_id>/sets/<int:set_id>/rate", methods=["POST"])
@login_required
def set_rate(session_id: int, set_id: int):
    ws, log_row = _session_set_or_404(session_id, set_id)
    entry = log_row.entry
    if ws.ended_at is not None:
        return _set_error(ws, entry.id, "This workout is finished.")
    form = RateForm()
    if not form.validate_on_submit():
        return _set_error(ws, entry.id, _first_form_error(form))
    sets.rate_set(log_row, form.rating.data or None)
    db.session.commit()
    if _wants_json():
        return _set_payload(ws, entry, log_row.set_index, log_row)
    return _back(ws, entry.id)


# ---------------------------------------------------------------------------
# Exercise swap (session entries only, before any set is logged)
# ---------------------------------------------------------------------------
def _swappable_or_404(session_id: int, entry_id: int) -> tuple[WorkoutSession, SessionEntry]:
    ws, entry = _session_entry_or_404(session_id, entry_id)
    if ws.ended_at is not None or entry.plan is None or entry.set_logs:
        abort(404)
    return ws, entry


@bp.route("/sessions/<int:session_id>/entries/<int:entry_id>/swap", methods=["GET", "POST"])
@login_required
@limiter.limit("30 per hour", key_func=user_or_ip, methods=["POST"])
def entry_swap(session_id: int, entry_id: int):
    ws, entry = _swappable_or_404(session_id, entry_id)
    user = current_user._get_current_object()
    form = SwapForm()
    if request.method == "POST":
        if not form.validate_on_submit():
            flash("Pick a reason for the swap.", "error")
            return redirect(url_for("workouts.entry_swap", session_id=ws.id, entry_id=entry.id))
        _options, _source, notice = trainer.swap_options(
            user,
            entry,
            reason=form.reason.data,
            use_ai=bool(current_app.config.get("ANTHROPIC_API_KEY")),
            now=utcnow(),
        )
        db.session.commit()
        if notice:
            flash(notice, "info")
        return redirect(url_for("workouts.entry_swap", session_id=ws.id, entry_id=entry.id))

    options = load_exercises(entry.plan.swaps_json)
    source, notice = entry.plan.swaps_source, ""
    if not options:
        options, source, notice = trainer.swap_options(
            user, entry, reason=form.reason.data or "preference", use_ai=False, now=utcnow()
        )
        db.session.commit()
    return render_template(
        "workouts/swap.html",
        ws=ws,
        entry=entry,
        current=plan_row_view(trainer.planned_from_entry(entry)),
        form=form,
        options=[plan_row_view(o) for o in options],
        source=source,
        notice=notice,
    )


@bp.route("/sessions/<int:session_id>/entries/<int:entry_id>/swap/<int:choice>", methods=["POST"])
@login_required
def entry_swap_apply(session_id: int, entry_id: int, choice: int):
    ws, entry = _swappable_or_404(session_id, entry_id)
    if not 0 <= choice < len(load_exercises(entry.plan.swaps_json)):
        abort(404)
    old_name = entry.exercise_name
    try:
        entry = trainer.apply_swap(current_user._get_current_object(), entry, choice)
    except (IndexError, ValueError):
        db.session.rollback()
        abort(404)
    db.session.commit()
    flash(f"Swapped {old_name} for {entry.exercise_name}.", "success")
    return _back(ws, entry.id)


# ---------------------------------------------------------------------------
# Finish, calories, delete
# ---------------------------------------------------------------------------
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
    DailyPlan.query.filter_by(user_id=current_user.id, session_id=ws.id).update(
        {DailyPlan.session_id: None}, synchronize_session="fetch"
    )
    db.session.delete(ws)  # cascades to entries, their SetLogs and EntryPlans
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
