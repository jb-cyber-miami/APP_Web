"""Regression tests for the review round: plan units and what counts as a gym day."""

from datetime import timedelta

from app.extensions import db
from app.models import EntryPlan, SessionEntry, SetLog, User, WorkoutSession
from app.services import activity, calories
from app.services.plan_schema import load_plan, load_targets
from app.timeutil import today_for


def _plan_with_weight(client, weight=135.0, unit="lb"):
    """A stored plan whose first exercise has a weight target (as if from history)."""
    assert client.post("/plan/rebuild", data={"minutes": 30, "focus": "auto"}).status_code == 302
    from app.models import DailyPlan

    row = DailyPlan.query.one()
    plan = load_plan(row.plan_json)
    first = plan.exercises[0]
    sets = [
        s.model_copy(update={"weight": weight, "unit": unit, "target_reps": 8, "target_seconds": None})
        for s in first.sets
    ]
    exercises = [first.model_copy(update={"sets": sets})] + list(plan.exercises[1:])
    row.plan_json = plan.model_copy(update={"exercises": exercises}).model_dump_json()
    db.session.commit()
    return first.name


def _first_entry(ws):
    return SessionEntry.query.filter_by(session_id=ws.id).order_by(SessionEntry.id).first()


def test_plan_built_in_lb_started_in_kg_logs_kg(alice):
    _plan_with_weight(alice, 135.0, "lb")
    assert alice.post("/settings/trainer", data={"unit": "kg", "default_minutes": 45}).status_code == 302
    assert alice.post("/plan/start").status_code == 302
    ws = WorkoutSession.query.one()
    entry = _first_entry(ws)
    assert entry.plan.unit == "kg"
    targets = load_targets(entry.plan.targets_json)
    assert all(t.unit == "kg" for t in targets)
    kg = targets[0].weight
    assert kg is not None and abs(kg - 61.2) <= 2.5  # 135 lb, rounded to plates

    html = alice.get(f"/sessions/{ws.id}").get_data(as_text=True)
    assert 'placeholder="kg"' in html

    # The user switches back to lb mid-workout: the entry keeps its own unit.
    alice.post("/settings/trainer", data={"unit": "lb", "default_minutes": 45})
    weight = f"{kg:g}"
    resp = alice.post(
        f"/sessions/{ws.id}/entries/{entry.id}/sets", data={"set_index": 0, "reps": 8, "weight": weight}
    )
    assert resp.status_code == 302
    log = SetLog.query.one()
    assert (log.unit, log.weight, log.target_weight) == ("kg", kg, kg)
    assert 'placeholder="kg"' in alice.get(f"/sessions/{ws.id}").get_data(as_text=True)


def test_plan_in_same_unit_is_unchanged(alice):
    _plan_with_weight(alice, 135.0, "lb")
    alice.post("/plan/start")
    entry = _first_entry(WorkoutSession.query.one())
    assert entry.plan.unit == "lb"
    assert {t.weight for t in load_targets(entry.plan.targets_json)} == {135.0}


def _user():
    return User.query.filter_by(username="alice").one()


def test_started_plan_alone_is_not_a_gym_day(alice):
    assert alice.post("/plan/start").status_code == 302
    user = _user()
    day = today_for(user.timezone)
    ws = WorkoutSession.query.one()
    assert ws.entries and SetLog.query.count() == 0
    assert not calories.day_summary(user, day).went_to_gym
    assert activity.streak_weeks(user, day) == 0
    assert not next(c for c in activity.week_strip(user, day) if c.day == day).is_gym

    # Finishing it untouched still isn't training ...
    alice.post(f"/sessions/{ws.id}/finish")
    assert not calories.day_summary(user, day).went_to_gym
    assert activity.streak_weeks(user, day) == 0

    # ... but one logged set is.
    ws.ended_at = None
    db.session.commit()
    entry = _first_entry(ws)
    data = {"set_index": 0}
    target = load_targets(entry.plan.targets_json)[0]
    data["seconds" if target.target_seconds else "reps"] = target.target_seconds or target.target_reps
    assert alice.post(f"/sessions/{ws.id}/entries/{entry.id}/sets", data=data).status_code == 302
    assert calories.day_summary(user, day).went_to_gym
    assert activity.streak_weeks(user, day) == 1


def test_finished_untouched_plan_with_calories_counts(alice):
    alice.post("/plan/start")
    ws = WorkoutSession.query.one()
    alice.post(f"/sessions/{ws.id}/finish")
    ws.calories = 250
    db.session.commit()
    user = _user()
    assert calories.day_summary(user, ws.day).went_to_gym
    assert activity.streak_weeks(user, ws.day) == 1


def test_legacy_rules_unchanged(alice):
    """An empty finished session, or an entry without an EntryPlan, still counts."""
    alice.post("/sessions/start")
    ws = WorkoutSession.query.one()
    alice.post(f"/sessions/{ws.id}/finish")
    user = _user()
    assert calories.day_summary(user, ws.day).went_to_gym

    old = WorkoutSession(user_id=user.id, day=ws.day - timedelta(days=8), started_at=ws.started_at)
    db.session.add(old)
    db.session.flush()
    db.session.add(SessionEntry(session_id=old.id, exercise_name="Squat", sets_done=3))
    db.session.commit()
    assert EntryPlan.query.count() == 0
    assert calories.day_summary(user, old.day).went_to_gym
    assert activity.streak_weeks(user, ws.day) == 2


def _public(client, app, day):
    import re

    page = client.post("/share", data={"day": day.isoformat()}).get_data(as_text=True)
    path = re.search(r'id="share-url" value="http://localhost(/s/[^"]+)"', page).group(1)
    return app.test_client().get(path).get_data(as_text=True)


def test_share_page_for_started_plan_says_no_gym(alice, app):
    alice.post("/plan/start")
    html = _public(alice, app, today_for(_user().timezone))
    assert "didn't go to the gym" in html and "trained today" not in html


def test_share_page_lists_only_logged_exercises(alice, app):
    alice.post("/plan/start")
    ws = WorkoutSession.query.one()
    entry = _first_entry(ws)
    target = load_targets(entry.plan.targets_json)[0]
    data = {
        "set_index": 0,
        ("seconds" if target.target_seconds else "reps"): target.target_seconds or target.target_reps,
    }
    alice.post(f"/sessions/{ws.id}/entries/{entry.id}/sets", data=data)
    others = [e.exercise_name for e in ws.entries if e.id != entry.id]
    html = _public(alice, app, ws.day)
    assert "trained today" in html and entry.exercise_name in html
    assert "Not logged" not in html
    assert not any(name in html for name in others)
    summary = calories.day_summary(_user(), ws.day)
    assert [e.id for e in summary.logged_entries] == [entry.id]
    assert summary.exercise_names == [entry.exercise_name]
