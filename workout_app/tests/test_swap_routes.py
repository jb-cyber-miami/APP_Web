import json
from contextlib import contextmanager

import pytest
from flask import template_rendered

from app.models import EntryPlan, SessionEntry, SetLog, WorkoutSession


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


@pytest.fixture
def started(alice):
    alice.post("/plan/start")
    ws = WorkoutSession.query.one()
    entry = SessionEntry.query.filter_by(session_id=ws.id).order_by(SessionEntry.id).first()
    return ws, entry


def _swap(ws, entry):
    return f"/sessions/{ws.id}/entries/{entry.id}/swap"


def test_get_stores_fallback_options(app, alice, started):
    ws, entry = started
    with rendered(app) as seen:
        assert alice.get(_swap(ws, entry)).status_code == 200
    ctx = next(c for n, c in seen if n == "workouts/swap.html")
    stored = json.loads(entry.plan.swaps_json)
    assert 1 <= len(stored) <= 3 and entry.plan.swaps_source == "fallback"
    assert [o.name for o in ctx["options"]] == [o["name"] for o in stored]
    assert ctx["current"].name == entry.exercise_name and ctx["source"] == "fallback"
    assert ctx["entry"].id == entry.id and ctx["ws"].id == ws.id
    assert entry.exercise_name not in [o["name"] for o in stored]
    # a second GET reuses the stored options
    alice.get(_swap(ws, entry))
    assert json.loads(entry.plan.swaps_json) == stored


def test_post_reason_recomputes_options(alice, started):
    ws, entry = started
    resp = alice.post(_swap(ws, entry), data={"reason": "busy"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(_swap(ws, entry))
    assert json.loads(entry.plan.swaps_json)
    assert alice.post(_swap(ws, entry), data={"reason": "<bogus>"}).status_code == 302


def test_apply_choice(alice, started):
    ws, entry = started
    alice.get(_swap(ws, entry))
    first = json.loads(entry.plan.swaps_json)[0]["name"]
    resp = alice.post(f"{_swap(ws, entry)}/0")
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"#ex-{entry.id}")
    assert entry.exercise_name == first
    assert entry.plan.swaps_json == "[]"
    assert entry.exercise_id is not None


def test_out_of_range_choice_404(alice, started):
    ws, entry = started
    alice.get(_swap(ws, entry))
    n = len(json.loads(entry.plan.swaps_json))
    assert alice.post(f"{_swap(ws, entry)}/{n}").status_code == 404
    assert alice.post(f"{_swap(ws, entry)}/99").status_code == 404


def test_apply_without_stored_options_404(alice, started):
    ws, entry = started
    assert alice.post(f"{_swap(ws, entry)}/0").status_code == 404


def test_swap_after_logged_set_404(alice, started):
    ws, entry = started
    alice.get(_swap(ws, entry))
    targets = json.loads(entry.plan.targets_json)
    data = {"set_index": 0}
    if targets[0].get("target_seconds"):
        data["seconds"] = targets[0]["target_seconds"]
    else:
        data["reps"] = targets[0]["target_reps"]
    alice.post(f"/sessions/{ws.id}/entries/{entry.id}/sets", data=data)
    assert SetLog.query.count() == 1
    name = entry.exercise_name
    assert alice.post(f"{_swap(ws, entry)}/0").status_code == 404
    assert alice.get(_swap(ws, entry)).status_code == 404
    assert alice.post(_swap(ws, entry), data={"reason": "busy"}).status_code == 404
    assert entry.exercise_name == name


def test_swap_on_finished_session_404(alice, started):
    ws, entry = started
    alice.post(f"/sessions/{ws.id}/finish")
    assert alice.get(_swap(ws, entry)).status_code == 404


def test_other_users_entry_404(alice, bob, started):
    ws, entry = started
    alice.get(_swap(ws, entry))
    assert bob.get(_swap(ws, entry)).status_code == 404
    assert bob.post(_swap(ws, entry), data={"reason": "busy"}).status_code == 404
    assert bob.post(f"{_swap(ws, entry)}/0").status_code == 404
    bob.post("/sessions/start")
    bob_ws = WorkoutSession.query.filter(WorkoutSession.user_id != ws.user_id).one()
    assert bob.get(f"/sessions/{bob_ws.id}/entries/{entry.id}/swap").status_code == 404
    assert bob.post(f"/sessions/{bob_ws.id}/entries/{entry.id}/swap/0").status_code == 404


def test_legacy_entry_cannot_be_swapped(alice):
    from app.extensions import db

    alice.post("/sessions/start")
    ws = WorkoutSession.query.one()
    entry = SessionEntry(session_id=ws.id, exercise_name="Old Bench", sets_done=3)
    db.session.add(entry)
    db.session.commit()
    assert EntryPlan.query.count() == 0
    assert alice.get(_swap(ws, entry)).status_code == 404
