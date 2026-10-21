import json
from types import SimpleNamespace

import pytest

from app.models import Exercise, GymProfile
from app.services import ai_coach

PLAN = {
    "title": "Push day",
    "exercises": [
        {
            "name": "Dumbbell Bench Press",
            "muscle_group": "Chest",
            "equipment": "Dumbbells",
            "sets": 4,
            "reps": "8-10",
            "rest_seconds": 90,
            "instructions": "Keep shoulder blades back.",
        },
        {
            "name": "Push-up",
            "muscle_group": "Chest",
            "equipment": "Bodyweight",
            "sets": 3,
            "reps": "12",
            "rest_seconds": 60,
            "instructions": "Body in a straight line.",
        },
    ],
}


class FakeMessages:
    def __init__(self, text, stop_reason="end_turn"):
        self.text, self.stop_reason, self.calls = text, stop_reason, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=self.text)],
        )


@pytest.fixture
def fake_claude(monkeypatch, app):
    app.config["ANTHROPIC_API_KEY"] = "test-key"
    fake = FakeMessages(json.dumps(PLAN))
    client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    monkeypatch.setattr(ai_coach, "_client", lambda: client)
    return fake


def _profile(**overrides):
    base = dict(
        gym_size="home",
        equipment=["dumbbells", "bench"],
        goal="strength",
        experience="beginner",
        notes="bad left knee",
        focus="push",
        count=2,
        minutes=30,
        extra="",
    )
    base.update(overrides)
    return base


def test_generate_workout_parses_and_validates(fake_claude, app):
    plan = ai_coach.generate_workout(**_profile())
    assert [e.name for e in plan.exercises] == ["Dumbbell Bench Press", "Push-up"]
    call = fake_claude.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default"
    assert "dumbbells, bench" in call["messages"][0]["content"]
    assert "<user_notes>" in call["messages"][0]["content"]


def test_generate_workout_handles_refusal(fake_claude, app):
    fake_claude.stop_reason = "refusal"
    with pytest.raises(ai_coach.AIError):
        ai_coach.generate_workout(**_profile())


def test_generate_workout_rejects_bad_output(fake_claude, app):
    fake_claude.text = json.dumps({"title": "x", "exercises": [{"name": "A", "sets": 999}]})
    with pytest.raises(ai_coach.AIError):
        ai_coach.generate_workout(**_profile())


def test_missing_api_key_is_friendly(app):
    app.config["ANTHROPIC_API_KEY"] = None
    with pytest.raises(ai_coach.AIError, match="isn't configured"):
        ai_coach.generate_workout(**_profile())


def test_ai_flow_preview_then_save_selected(alice, fake_claude):
    alice.post(
        "/gym",
        data={
            "gym_size": "home",
            "equipment": ["dumbbells", "bench"],
            "goal": "strength",
            "experience": "beginner",
            "notes": "",
        },
    )
    assert GymProfile.query.one().equipment == ["bench", "dumbbells"]

    preview = alice.post("/exercises/ai", data={"focus": "full body", "count": 3, "minutes": 30})
    assert preview.status_code == 200
    assert b"Dumbbell Bench Press" in preview.data

    form = {k: [e[k] for e in PLAN["exercises"]] for k in PLAN["exercises"][0]}
    form["selected"] = ["1"]  # only the push-up
    resp = alice.post("/exercises/ai/save", data=form)
    assert resp.status_code == 302
    saved = Exercise.query.all()
    assert [e.name for e in saved] == ["Push-up"]
    assert saved[0].source == "ai"


def test_ai_save_revalidates_tampered_fields(alice):
    form = {
        "name": ["X"],
        "muscle_group": [""],
        "equipment": [""],
        "sets": ["9999"],
        "reps": ["5"],
        "rest_seconds": ["60"],
        "instructions": [""],
        "selected": ["0"],
    }
    alice.post("/exercises/ai/save", data=form)
    assert Exercise.query.count() == 0
