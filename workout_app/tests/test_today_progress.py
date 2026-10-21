from contextlib import contextmanager
from datetime import timedelta

import pytest
from flask import template_rendered

from app.extensions import db
from app.models import DailyPlan, EntryPlan, Exercise, SessionEntry, User, WorkoutSession
from app.services.plan_schema import load_plan
from app.timeutil import today_for


@contextmanager
def rendered(app):
    seen = []

    def record(sender, template, context, **extra):
        seen.append((template.name, context))

    template_rendered.connect(record, app)
    try:
        yield seen
    finally:
        template_rendered.disconnect(record, app)


def _ctx(seen, name):
    return next(c for n, c in seen if n == name)


def test_today_for_new_user_without_key(app, alice):
    with rendered(app) as seen:
        resp = alice.get("/")
    assert resp.status_code == 200
    ctx = _ctx(seen, "workouts/today.html")
    hero = ctx["hero"]
    assert hero.state == "plan" and not hero.plan_saved
    assert hero.plan.source == "fallback" and len(hero.plan.exercises) >= 3
    assert len(hero.rows) == min(3, len(hero.plan.exercises))
    assert hero.more == len(hero.plan.exercises) - len(hero.rows)
    assert hero.total_sets == sum(len(e.sets) for e in hero.plan.exercises)
    assert len(ctx["recovery"].items) == 11 and not ctx["recovery"].has_history
    assert len(ctx["week"]) == 7 and ctx["streak"] == 0
    assert ctx["ai_left"] is None and ctx["unit"] == "lb"
    assert ctx["plan_form"].minutes.data == 45
    assert ctx["active"] is None
    assert DailyPlan.query.count() == 0  # GET never writes


def test_get_today_never_calls_ai(app, alice, monkeypatch):
    from app.services import ai_coach

    app.config["ANTHROPIC_API_KEY"] = "sk-test"

    def boom(*a, **k):
        raise AssertionError("no AI on GET")

    monkeypatch.setattr(ai_coach, "_client", boom)
    with rendered(app) as seen:
        assert alice.get("/").status_code == 200
    assert _ctx(seen, "workouts/today.html")["ai_left"] == 5


def test_rebuild_then_start(app, alice):
    assert alice.post("/plan/rebuild", data={"minutes": 30, "focus": "auto"}).status_code == 302
    row = DailyPlan.query.one()
    assert row.source == "fallback" and row.session_id is None and row.generations == 0
    plan = load_plan(row.plan_json)

    resp = alice.post("/plan/start")
    ws = WorkoutSession.query.one()
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/sessions/{ws.id}")
    assert row.session_id == ws.id
    entries = SessionEntry.query.filter_by(session_id=ws.id).order_by(SessionEntry.id).all()
    assert [e.exercise_name for e in entries] == [e.name for e in plan.exercises]
    assert EntryPlan.query.count() == len(entries)
    assert all(e.exercise_id is not None and e.plan.plan_index == i for i, e in enumerate(entries))

    # starting again reuses the open session
    alice.post("/plan/start")
    assert WorkoutSession.query.count() == 1

    with rendered(app) as seen:
        alice.get("/")
    hero = _ctx(seen, "workouts/today.html")["hero"]
    assert hero.state == "active" and hero.session.id == ws.id
    assert (hero.exercises_done, hero.exercises_total) == (0, len(entries))
    assert hero.title == plan.title

    alice.post(f"/sessions/{ws.id}/finish")
    with rendered(app) as seen:
        alice.get("/")
    hero = _ctx(seen, "workouts/today.html")["hero"]
    assert hero.state == "done" and hero.session.id == ws.id and hero.done_minutes == 0


def test_start_without_stored_plan_persists_fallback(alice):
    assert alice.post("/plan/start").status_code == 302
    row = DailyPlan.query.one()
    assert row.session_id == WorkoutSession.query.one().id


def test_generate_without_key_falls_back(alice):
    resp = alice.post("/plan/generate", data={"minutes": 45, "focus": "full body", "extra": "bad knee"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")
    row = DailyPlan.query.one()
    assert row.source == "fallback" and row.generations == 0


@pytest.mark.parametrize(
    "data",
    [{"minutes": 5, "focus": "auto"}, {"minutes": 45, "focus": "nope"}, {"minutes": 45, "extra": "x" * 301}],
)
def test_plan_form_validation(alice, data):
    assert alice.post("/plan/rebuild", data=data).status_code == 302
    assert DailyPlan.query.count() == 0


def test_plan_routes_are_post_only(alice):
    for path in ("/plan/generate", "/plan/rebuild", "/plan/start"):
        assert alice.get(path).status_code == 405


def test_done_state_with_plain_session(app, alice):
    alice.post("/sessions/start")
    ws = WorkoutSession.query.one()
    alice.post(f"/sessions/{ws.id}/finish")
    with rendered(app) as seen:
        alice.get("/")
    assert _ctx(seen, "workouts/today.html")["hero"].state == "done"


def test_unstarted_stored_plan_beats_done(app, alice):
    alice.post("/sessions/start")
    ws = WorkoutSession.query.one()
    alice.post(f"/sessions/{ws.id}/finish")
    alice.post("/plan/rebuild", data={"minutes": 30, "focus": "auto"})
    with rendered(app) as seen:
        alice.get("/")
    hero = _ctx(seen, "workouts/today.html")["hero"]
    assert hero.state in ("plan", "rest") and hero.plan_saved


def test_progress_without_data(app, alice):
    with rendered(app) as seen:
        assert alice.get("/progress").status_code == 200
    ctx = _ctx(seen, "workouts/progress.html")
    assert ctx["report"].overall is None and ctx["overall_pct"] == 0
    assert len(ctx["rows"]) == 11 and ctx["top_lifts"] == []
    today = today_for(User.query.one().timezone)
    assert (ctx["month"].year, ctx["month"].month) == (today.year, today.month)
    assert ctx["month"].next is None and ctx["streak"] == 0 and ctx["unit"] == "lb"


def test_progress_with_data(app, alice):
    uid = User.query.one().id
    from app.models import utcnow

    base = utcnow()
    for i, (name, group, weights) in enumerate(
        [
            ("Bench Press", "Chest", (185, 205)),
            ("Back Squat", "Quads", (225, 245)),
            ("Barbell Row", "Back", (135, 145)),
        ]
    ):
        ex = Exercise(user_id=uid, name=name, muscle_group=group)
        db.session.add(ex)
        db.session.flush()
        for days_ago, w in ((40, weights[0]), (2, weights[1])):
            start = base - timedelta(days=days_ago, hours=i + 1)
            ws = WorkoutSession(
                user_id=uid, day=start.date(), started_at=start, ended_at=start + timedelta(hours=1)
            )
            db.session.add(ws)
            db.session.flush()
            db.session.add(
                SessionEntry(
                    session_id=ws.id,
                    exercise_id=ex.id,
                    exercise_name=name,
                    sets_done=3,
                    reps_done="5",
                    weight=f"{w}",
                )
            )
    db.session.commit()
    with rendered(app) as seen:
        assert alice.get("/progress").status_code == 200
    ctx = _ctx(seen, "workouts/progress.html")
    assert ctx["report"].overall is not None and ctx["overall_pct"] > 0
    assert [lf.name for lf in ctx["top_lifts"]] == ["Back Squat", "Bench Press", "Barbell Row"]
    assert ctx["streak"] >= 1


@pytest.mark.parametrize("raw", ["2999-01", "1999-12", "abcd-ef", "2026-13", "2026-1", "<script>"])
def test_progress_month_invalid_or_future_ignored(app, alice, raw):
    today = today_for(User.query.one().timezone)
    with rendered(app) as seen:
        assert alice.get(f"/progress?month={raw}").status_code == 200
    month = _ctx(seen, "workouts/progress.html")["month"]
    assert (month.year, month.month) == (today.year, today.month)


def test_progress_month_past(app, alice):
    with rendered(app) as seen:
        alice.get("/progress?month=2026-01")
    month = _ctx(seen, "workouts/progress.html")["month"]
    assert (month.year, month.month, month.title, month.prev) == (2026, 1, "January 2026", "2025-12")
    assert month.next == "2026-02"
    assert all(len(w) == 7 for w in month.weeks)


def test_history_default_and_single_day(app, alice):
    page = alice.get("/history").data
    assert b"Last 14 days" in page
    today = today_for(User.query.one().timezone)
    yesterday = today - timedelta(days=1)
    with rendered(app) as seen:
        assert alice.get(f"/history?day={yesterday.isoformat()}").status_code == 200
    ctx = _ctx(seen, "workouts/history.html")
    assert ctx["single_day"] is True and [d.day for d in ctx["days"]] == [yesterday]
    for bad in ((today + timedelta(days=1)).isoformat(), "nope", "2026-02-30"):
        with rendered(app) as seen:
            alice.get(f"/history?day={bad}")
        ctx = _ctx(seen, "workouts/history.html")
        assert ctx["single_day"] is False and len(ctx["days"]) == 14


def test_exercise_detail(app, alice, bob):
    alice.post(
        "/exercises/new",
        data={"name": "Bench Press", "muscle_group": "Chest", "sets": 3, "reps": "8", "rest_seconds": 90},
    )
    ex = Exercise.query.one()
    alice.post("/sessions/start")
    ws = WorkoutSession.query.one()
    alice.post(
        f"/sessions/{ws.id}",
        data={"exercise_id": ex.id, "sets_done": 3, "reps_done": "8", "weight": "135", "log_all": "y"},
    )
    alice.post(f"/sessions/{ws.id}/finish")
    with rendered(app) as seen:
        assert alice.get(f"/exercises/{ex.id}").status_code == 200
    ctx = _ctx(seen, "workouts/exercise_detail.html")
    assert ctx["exercise"].id == ex.id and ctx["muscles"].primary == "chest"
    assert ctx["targets"] == ("chest", "shoulders", "triceps")
    assert ctx["e1rm"] == 171.0 and ctx["best_set"] == "135 lb × 8"
    assert ctx["spark_points"] is None  # one exposure only
    row = ctx["history_rows"][0]
    assert row.session_id == ws.id and row.sets == ["135 × 8"] * 3 and row.e1rm == 171.0
    assert bob.get(f"/exercises/{ex.id}").status_code == 404
    assert alice.get("/exercises/999").status_code == 404


def test_exercise_edit_redirects_to_detail(alice):
    alice.post("/exercises/new", data={"name": "Row", "sets": 3, "reps": "8", "rest_seconds": 90})
    ex = Exercise.query.one()
    resp = alice.post(
        f"/exercises/{ex.id}/edit",
        data={"name": "Barbell Row", "muscle_group": "Back", "sets": 3, "reps": "8", "rest_seconds": 90},
    )
    assert resp.headers["Location"].endswith(f"/exercises/{ex.id}")
    assert ex.muscles_row.primary == "back"


def test_exercise_delete_nulls_entry_ids(alice):
    alice.post("/exercises/new", data={"name": "Deadlift", "sets": 3, "reps": "5", "rest_seconds": 120})
    ex = Exercise.query.one()
    alice.post("/sessions/start")
    ws = WorkoutSession.query.one()
    alice.post(f"/sessions/{ws.id}", data={"exercise_id": ex.id, "sets_done": 3})
    entry = SessionEntry.query.one()
    alice.post(f"/exercises/{ex.id}/delete")
    db.session.refresh(entry)
    assert entry.exercise_id is None and entry.exercise_name == "Deadlift"
    from app.models import ExerciseMuscles

    assert ExerciseMuscles.query.count() == 0


def test_session_delete_unlinks_daily_plan(alice):
    alice.post("/plan/start")
    ws = WorkoutSession.query.one()
    alice.post(f"/sessions/{ws.id}/delete")
    row = DailyPlan.query.one()
    assert row.session_id is None


def test_exercises_grouped_and_filtered(app, alice):
    for name, group in (("Bench Press", "Chest"), ("Barbell Row", "Back"), ("Mystery Move", "")):
        alice.post(
            "/exercises/new",
            data={"name": name, "muscle_group": group, "sets": 3, "reps": "8", "rest_seconds": 60},
        )
    with rendered(app) as seen:
        alice.get("/exercises")
    ctx = _ctx(seen, "workouts/exercises.html")
    assert [g[0] for g in ctx["groups"]] == ["chest", "back", "other"]
    assert ctx["muscle_filter"] is None and len(ctx["exercises"]) == 3
    with rendered(app) as seen:
        alice.get("/exercises?muscle=back")
    ctx = _ctx(seen, "workouts/exercises.html")
    assert ctx["muscle_filter"] == "back" and [g[0] for g in ctx["groups"]] == ["back"]
    assert [e.name for e in ctx["exercises"]] == ["Barbell Row"]
    with rendered(app) as seen:
        alice.get("/exercises?muscle=<x>")
    assert _ctx(seen, "workouts/exercises.html")["muscle_filter"] is None


def test_trainer_settings(app, alice):
    assert alice.post("/settings/trainer", data={"unit": "kg", "default_minutes": 60}).status_code == 302
    from app.models import TrainerSettings

    row = TrainerSettings.query.one()
    assert (row.unit, row.default_minutes) == ("kg", 60)
    alice.post("/settings/trainer", data={"unit": "stone", "default_minutes": 500})
    assert (row.unit, row.default_minutes) == ("kg", 60)
    with rendered(app) as seen:
        alice.get("/settings")
    ctx = _ctx(seen, "auth/settings.html")
    assert ctx["trainer_form"].unit.data == "kg" and ctx["trainer_form"].default_minutes.data == 60
    assert ctx["apple_tokens"] == 0 and ctx["oura"] is None and ctx["share_links"] == 0
    assert ctx["user_unit"] == "kg"


def test_template_globals(app, alice):
    with rendered(app) as seen:
        alice.get("/history")
    ctx = _ctx(seen, "workouts/history.html")
    assert ctx["now_session"] is None and ctx["now_session_title"] == "Workout"
    assert ctx["integration_alert"] is False and len(ctx["canonical_muscles"]) == 11
    alice.post("/plan/start")
    with rendered(app) as seen:
        alice.get("/history")
    ctx = _ctx(seen, "workouts/history.html")
    assert ctx["now_session"] is not None
    assert ctx["now_session_title"] == load_plan(DailyPlan.query.one().plan_json).title


def test_filters(app):
    from datetime import datetime

    env = app.jinja_env
    assert env.filters["num"](9840) == "9,840" and env.filters["num"](None) == "—"
    assert env.filters["pct"](140) == 100 and env.filters["pct"](-3) == 0 and env.filters["pct"](None) == 0
    assert env.filters["iso_utc"](datetime(2026, 10, 21, 7, 2, 0, 123)) == "2026-10-21T07:02:00Z"
    assert env.filters["muscle_label"]("quads") == "Quads" and env.filters["muscle_label"]("<x>") == ""
    assert env.filters["weight"](135.0, "lb") == "135 lb" and env.filters["weight"](None, "lb") == ""


def test_anonymous_and_error_pages_render(client):
    assert client.get("/login").status_code == 200
    assert client.get("/nope").status_code == 404
