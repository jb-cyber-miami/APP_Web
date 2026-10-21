import json

import pytest

from app.extensions import db
from app.models import EntryPlan, Exercise, SessionEntry, SetLog, WorkoutSession
from tests.conftest import csrf_from

FETCH = {"X-Requested-With": "fetch"}


def _setup(client, *, reps="8", weight="135", sets=3, name="Bench Press"):
    client.post("/exercises/new", data={"name": name, "sets": sets, "reps": reps, "rest_seconds": 90})
    ex = Exercise.query.filter_by(name=name).one()
    client.post("/sessions/start")
    ws = WorkoutSession.query.filter_by(ended_at=None).one()
    resp = client.post(
        f"/sessions/{ws.id}",
        data={"exercise_id": ex.id, "sets_done": sets, "reps_done": reps, "weight": weight},
    )
    assert resp.status_code == 302
    entry = SessionEntry.query.filter_by(session_id=ws.id).order_by(SessionEntry.id.desc()).first()
    return ws, entry


def _url(ws, entry):
    return f"/sessions/{ws.id}/entries/{entry.id}/sets"


def test_added_entry_is_tracked_with_targets(alice):
    ws, entry = _setup(alice)
    assert entry.plan is not None
    targets = json.loads(entry.plan.targets_json)
    assert len(targets) == 3
    assert {(t["target_reps"], t["weight"]) for t in targets} == {(8, 135.0)}
    assert entry.sets_done == 0 and entry.set_logs == []


def test_check_set_html_copies_targets_from_plan(alice):
    ws, entry = _setup(alice)
    resp = alice.post(
        _url(ws, entry),
        data={
            "set_index": 0,
            "reps": 7,
            "weight": "140",
            "target_reps": 99,
            "target_weight": 999,
            "unit": "kg",
        },
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/sessions/{ws.id}#ex-{entry.id}")
    log = SetLog.query.one()
    assert (log.reps, log.weight, log.unit) == (7, 140.0, "lb")  # unit from settings, not the form
    assert (log.target_reps, log.target_weight) == (8, 135.0)  # from EntryPlan, not the form
    assert log.user_id == ws.user_id
    assert (entry.sets_done, entry.reps_done, entry.weight) == (1, "7", "140 lb")


def test_check_set_json(alice):
    ws, entry = _setup(alice)
    resp = alice.post(_url(ws, entry), data={"set_index": 1, "reps": 8, "weight": "135"}, headers=FETCH)
    assert resp.status_code == 200
    data = resp.get_json()
    log = SetLog.query.one()
    assert data == {
        "ok": True,
        "set_id": log.id,
        "set_index": 1,
        "is_done": True,
        "rating": None,
        "entry_done": 1,
        "entry_planned": 3,
        "exercises_done": 0,
        "exercises_total": 1,
        "unlogged_sets": 2,
        "rest_seconds": 90,
        "action": f"/sessions/{ws.id}/sets/{log.id}/delete",
        "rate_action": f"/sessions/{ws.id}/sets/{log.id}/rate",
    }


def test_set_index_zero_accepted_twenty_rejected(alice):
    ws, entry = _setup(alice)
    assert alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8}, headers=FETCH).status_code == 200
    resp = alice.post(_url(ws, entry), data={"set_index": 20, "reps": 8}, headers=FETCH)
    assert resp.status_code == 400 and resp.get_json()["ok"] is False
    # beyond the planned sets
    assert alice.post(_url(ws, entry), data={"set_index": 3, "reps": 8}, headers=FETCH).status_code == 400
    # missing set_index
    assert alice.post(_url(ws, entry), data={"reps": 8}, headers=FETCH).status_code == 400
    assert SetLog.query.count() == 1


def test_blank_reps_fall_back_to_target_and_upsert(alice):
    ws, entry = _setup(alice)
    alice.post(_url(ws, entry), data={"set_index": 0, "weight": "135"})
    alice.post(_url(ws, entry), data={"set_index": 0, "reps": 6, "weight": "135"})  # same set again
    log = SetLog.query.one()
    assert log.reps == 6


def test_bad_values_rejected(alice):
    ws, entry = _setup(alice)
    for data in (
        {"set_index": 0, "reps": 101},
        {"set_index": 0, "reps": 8, "weight": "2001"},
        {"set_index": 0, "reps": 8, "rating": "meh"},
        {"set_index": "x", "reps": 8},
    ):
        resp = alice.post(_url(ws, entry), data=data, headers=FETCH)
        assert resp.status_code == 400, data
        assert resp.get_json()["ok"] is False
    assert SetLog.query.count() == 0
    resp = alice.post(_url(ws, entry), data={"set_index": 0, "reps": 101})
    assert resp.status_code == 302  # no-JS: flash + redirect


def test_uncheck_set(alice):
    ws, entry = _setup(alice)
    alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8, "weight": "135"})
    log = SetLog.query.one()
    resp = alice.post(f"/sessions/{ws.id}/sets/{log.id}/delete", headers=FETCH)
    data = resp.get_json()
    assert data["ok"] and data["set_id"] is None and data["is_done"] is False
    assert data["action"] == f"/sessions/{ws.id}/entries/{entry.id}/sets"
    assert data["rate_action"] is None
    assert SetLog.query.count() == 0
    assert (entry.sets_done, entry.reps_done, entry.weight) == (0, "8", "")
    # HTML mode
    alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8})
    log = SetLog.query.one()
    resp = alice.post(f"/sessions/{ws.id}/sets/{log.id}/delete")
    assert resp.status_code == 302 and SetLog.query.count() == 0


def test_rate_set(alice):
    ws, entry = _setup(alice)
    alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8, "weight": "135"})
    log = SetLog.query.one()
    resp = alice.post(f"/sessions/{ws.id}/sets/{log.id}/rate", data={"rating": "ok"}, headers=FETCH)
    assert resp.get_json()["rating"] == "ok" and log.rating == "ok"
    resp = alice.post(f"/sessions/{ws.id}/sets/{log.id}/rate", data={"rating": "hard"})
    assert resp.status_code == 302 and log.rating == "hard"
    resp = alice.post(f"/sessions/{ws.id}/sets/{log.id}/rate", data={"rating": "great"}, headers=FETCH)
    assert resp.status_code == 400 and log.rating == "hard"
    alice.post(f"/sessions/{ws.id}/sets/{log.id}/rate", data={"rating": ""})
    assert log.rating is None
    # rating together with the check-off
    alice.post(_url(ws, entry), data={"set_index": 1, "reps": 8, "rating": "easy"})
    assert SetLog.query.filter_by(set_index=1).one().rating == "easy"


def test_add_set(alice):
    ws, entry = _setup(alice)
    assert alice.post(f"/sessions/{ws.id}/entries/{entry.id}/sets/add").status_code == 302
    assert len(json.loads(entry.plan.targets_json)) == 4
    resp = alice.post(_url(ws, entry), data={"set_index": 3, "reps": 8}, headers=FETCH)
    assert resp.get_json()["entry_planned"] == 4


def test_finished_session_rejected(alice):
    ws, entry = _setup(alice)
    alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8})
    log = SetLog.query.one()
    alice.post(f"/sessions/{ws.id}/finish")
    resp = alice.post(_url(ws, entry), data={"set_index": 1, "reps": 8}, headers=FETCH)
    assert resp.status_code == 400 and resp.get_json() == {"ok": False, "error": "This workout is finished."}
    assert alice.post(_url(ws, entry), data={"set_index": 1, "reps": 8}).status_code == 302
    assert alice.post(f"/sessions/{ws.id}/sets/{log.id}/delete", headers=FETCH).status_code == 400
    assert (
        alice.post(f"/sessions/{ws.id}/sets/{log.id}/rate", data={"rating": "ok"}, headers=FETCH).status_code
        == 400
    )
    assert alice.post(f"/sessions/{ws.id}/entries/{entry.id}/sets/add", headers=FETCH).status_code == 400
    assert SetLog.query.count() == 1


def test_other_users_session_entry_and_set_404(alice, bob):
    ws, entry = _setup(alice)
    alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8})
    log = SetLog.query.one()
    assert bob.post(_url(ws, entry), data={"set_index": 1, "reps": 8}).status_code == 404
    assert bob.post(f"/sessions/{ws.id}/sets/{log.id}/delete").status_code == 404
    assert bob.post(f"/sessions/{ws.id}/sets/{log.id}/rate", data={"rating": "ok"}).status_code == 404
    assert bob.post(f"/sessions/{ws.id}/entries/{entry.id}/sets/add").status_code == 404

    # bob's own session + alice's entry / set ids: child rows are checked too
    bob.post("/sessions/start")
    bob_ws = WorkoutSession.query.filter(WorkoutSession.user_id != ws.user_id).one()
    assert (
        bob.post(
            f"/sessions/{bob_ws.id}/entries/{entry.id}/sets", data={"set_index": 1, "reps": 8}
        ).status_code
        == 404
    )
    assert bob.post(f"/sessions/{bob_ws.id}/sets/{log.id}/delete").status_code == 404
    assert bob.post(f"/sessions/{bob_ws.id}/sets/{log.id}/rate", data={"rating": "ok"}).status_code == 404
    assert SetLog.query.count() == 1 and log.rating is None


def test_set_from_another_session_of_same_user_404(alice):
    ws, entry = _setup(alice)
    alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8})
    log = SetLog.query.one()
    alice.post(f"/sessions/{ws.id}/finish")
    alice.post("/sessions/start")
    ws2 = WorkoutSession.query.filter_by(ended_at=None).one()
    assert alice.post(f"/sessions/{ws2.id}/sets/{log.id}/delete").status_code == 404
    assert (
        alice.post(
            f"/sessions/{ws2.id}/entries/{entry.id}/sets", data={"set_index": 1, "reps": 8}
        ).status_code
        == 404
    )


def test_entry_delete_cascades(alice):
    ws, entry = _setup(alice)
    alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8})
    alice.post(f"/sessions/{ws.id}/entries/{entry.id}/delete")
    assert (SessionEntry.query.count(), SetLog.query.count(), EntryPlan.query.count()) == (0, 0, 0)


def test_session_delete_cascades(alice):
    ws, entry = _setup(alice)
    alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8})
    alice.post(f"/sessions/{ws.id}/delete")
    assert (WorkoutSession.query.count(), SetLog.query.count(), EntryPlan.query.count()) == (0, 0, 0)


def test_log_all(alice):
    alice.post("/exercises/new", data={"name": "Deadlift", "sets": 3, "reps": "5", "rest_seconds": 120})
    ex = Exercise.query.one()
    alice.post("/sessions/start")
    ws = WorkoutSession.query.one()
    alice.post(
        f"/sessions/{ws.id}",
        data={"exercise_id": ex.id, "sets_done": 3, "reps_done": "5", "weight": "225 lb", "log_all": "y"},
    )
    entry = SessionEntry.query.one()
    logs = SetLog.query.order_by(SetLog.set_index).all()
    assert [(s.set_index, s.reps, s.weight) for s in logs] == [(0, 5, 225.0), (1, 5, 225.0), (2, 5, 225.0)]
    assert (entry.sets_done, entry.reps_done, entry.weight) == (3, "5", "225 lb")


def test_unit_comes_from_trainer_settings(alice):
    alice.post("/settings/trainer", data={"unit": "kg", "default_minutes": 50})
    ws, entry = _setup(alice, weight="60 kg")
    alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8, "weight": "60"})
    log = SetLog.query.one()
    assert (log.weight, log.unit) == (60.0, "kg")
    assert entry.weight == "60 kg"


def test_set_routes_require_csrf(app, alice):
    ws, entry = _setup(alice)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8}).status_code == 400
        assert alice.post(f"/sessions/{ws.id}/entries/{entry.id}/sets/add").status_code == 400
        assert SetLog.query.count() == 0
        token = csrf_from(alice.get(f"/sessions/{ws.id}").data.decode())
        resp = alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8, "csrf_token": token})
        assert resp.status_code == 302 and SetLog.query.count() == 1
        log = SetLog.query.one()
        assert alice.post(f"/sessions/{ws.id}/sets/{log.id}/delete").status_code == 400
        assert alice.post(f"/sessions/{ws.id}/sets/{log.id}/rate", data={"rating": "ok"}).status_code == 400
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_set_routes_are_post_only(alice):
    ws, entry = _setup(alice)
    assert alice.get(_url(ws, entry)).status_code == 405


@pytest.mark.parametrize("path", ["/sets/1/delete", "/sets/1/rate", "/entries/1/sets", "/entries/1/sets/add"])
def test_unknown_ids_404(alice, path):
    alice.post("/sessions/start")
    ws = WorkoutSession.query.one()
    assert alice.post(f"/sessions/{ws.id}{path}", data={"set_index": 0}).status_code == 404


def test_session_view_context(app, alice):
    from flask import template_rendered

    ws, entry = _setup(alice)
    alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8, "weight": "135"})
    seen = []

    def record(sender, template, context, **extra):
        seen.append((template.name, context))

    template_rendered.connect(record, app)
    try:
        assert alice.get(f"/sessions/{ws.id}").status_code == 200
    finally:
        template_rendered.disconnect(record, app)
    ctx = next(c for name, c in seen if name == "workouts/session.html")
    assert ctx["progress"] == (0, 1) and ctx["unlogged"] == 2 and ctx["unit"] == "lb"
    assert ctx["focus_mode"] is True and ctx["summary"] is None and ctx["plan_title"] is None
    view = ctx["entries"][0]
    assert view.tracked and (view.done, view.planned) == (1, 3) and view.is_current and view.can_swap is False
    assert [r.is_done for r in view.rows] == [True, False, False]
    assert [r.is_current for r in view.rows] == [False, True, False]
    assert view.rows[1].target_text == "135 × 8"
    assert ctx["now_session"].id == ws.id

    seen.clear()
    alice.post(f"/sessions/{ws.id}/finish")
    template_rendered.connect(record, app)
    try:
        alice.get(f"/sessions/{ws.id}")
    finally:
        template_rendered.disconnect(record, app)
    ctx = next(c for name, c in seen if name == "workouts/session.html")
    summary = ctx["summary"]
    assert summary.sets_done == 1 and summary.volume == 1080 and summary.unit == "lb"
    assert summary.muscles_worked[:1] == ("chest",)
    assert len(summary.next_time) == 1
    assert ctx["focus_mode"] is False


def test_second_run_reports_pr(alice):
    from datetime import timedelta

    from app.models import utcnow

    ws, entry = _setup(alice)
    alice.post(_url(ws, entry), data={"set_index": 0, "reps": 8, "weight": "135"})
    alice.post(f"/sessions/{ws.id}/finish")
    ws.started_at -= timedelta(days=2)
    ws.ended_at = utcnow() - timedelta(days=2)
    db.session.commit()

    ex = Exercise.query.one()
    alice.post("/sessions/start")
    ws2 = WorkoutSession.query.filter_by(ended_at=None).one()
    alice.post(
        f"/sessions/{ws2.id}", data={"exercise_id": ex.id, "sets_done": 3, "reps_done": "8", "weight": "145"}
    )
    entry2 = SessionEntry.query.filter_by(session_id=ws2.id).one()
    alice.post(_url(ws2, entry2), data={"set_index": 0, "reps": 8, "weight": "145"})
    alice.post(f"/sessions/{ws2.id}/finish")

    from app.services.summary import workout_summary

    summary = workout_summary(ws2, unit="lb")
    assert [p.name for p in summary.prs] == ["Bench Press"]
    assert summary.prs[0].delta > 0
