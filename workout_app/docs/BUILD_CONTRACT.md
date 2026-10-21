# FitLog "AI Coach + Athletic Dark": integrated build contract

Status: **binding for iteration 1** (2026-10-21). Owner: tech lead.
Inputs: `docs/DESIGN.md` (designer), `docs/AI_COACH.md` (AI agent), and the current codebase
(71 tests green).

This document wins whenever it disagrees with DESIGN.md or AI_COACH.md. Each owner updates their
own spec to match it (see section 8). Product rule: FitLog is **inspired by** AI-trainer strength apps.
Do not copy any other product's name, logo, icons, illustrations, wording or assets.

---

## 0. Decisions and cuts

### 0.1 Conflicts resolved

| # | Topic | DESIGN.md says | AI_COACH.md says | **Decision** |
|---|---|---|---|---|
| C1 | Recovery formula | Window 48/72/96 h, 14-day data | Dose-based formula, 7-day lookback (§3.3) | **AI_COACH §3.3 formula.** It has worked examples we can use as fixtures. |
| C2 | Recovery colour classes | `.rec-0…4` in 20 % buckets, `.rec-na` | `muscle--fresh/recovering/fatigued`, `pct-0…100` | **`.rec-*` classes**, with buckets re-cut so colour never contradicts the status word (section 2.3). The `muscle--*` and `pct-*` classes are dropped. |
| C3 | Untrained muscle | `None` → `.rec-na` | 100, `fresh`, `last_trained_at=None` | Both apply: the value is 100 / `fresh` and the colour class is `rec-na` ("Fresh, not trained recently"). |
| C4 | Set rating values | Easy / Good / Hard | `easy` / `ok` / `hard` | Stored values are **`easy` / `ok` / `hard`**. The UI label for `ok` is **"Good"**, and its CSS class is `.rate-good`. |
| C5 | Preferences table | `user_pref` (unit, bodyweight, theme, weekly_goal) | `TrainerSettings` (unit, default_minutes) | Use **`TrainerSettings`** only. Bodyweight, theme and weekly goal are cut (0.2). |
| C6 | Strength score | 0–100 vs bodyweight standards, Starter…Elite levels | Self-relative index: 100 = your baseline | **Self-relative Strength Index** (AI_COACH §5). Level labels are cut. The ring and bar fill is `pct = clamp(round(index × 2 / 3), 0, 100)`, so a baseline of 100 fills about 67 % and 150 or more fills the ring (section 2.5). |
| C7 | Set rows | Pre-existing set rows posted to `/sessions/<id>/sets/<set_id>` | `POST /sessions/<id>/sets` with `plan_index` | A `SetLog` row exists **only for a completed set**. Un-done rows are rendered from the entry's targets (new `EntryPlan` table). Check a set: `POST /sessions/<sid>/entries/<eid>/sets` with `set_index`. Un-check: `POST …/sets/<set_id>/delete`. |
| C8 | Where targets live | `daily_plan.items_json` | `DailyPlan.plan_json` plus `plan_index` on each set | **Per-entry `EntryPlan` table.** It is created when a plan starts, and also when an exercise is added mid-session, so targets work for any entry. `plan_index` is no longer posted. |
| C9 | Swap | `GET /sessions/<id>/entries/<eid>/swap`, done sets kept | Plan-level swap; options in `DailyPlan.swaps_json` | **Session-entry swap only.** Options are stored in `EntryPlan.swaps_json`. A swap is allowed only while the entry has **0 logged sets**. Plan-level (before start) swaps are cut. |
| C10 | Hero state B "No plan yet" | Shows a Build button | `GET /` always yields an (unsaved) fallback plan | There is no state B. The hero always shows a plan (state `plan`). "Start workout" persists it. "Build with AI coach" / "Rebuild" sit in a `<details>` panel. |
| C11 | Rest state E | Every muscle under 40 % | Fallback "recovery day" when every template is excluded | **`plan.kind == "recovery"`**, set by `fallback_plan` (new `kind` field on `TodayPlan`). |
| C12 | Muscle synonyms | legs → +calves; full body → all | legs → quads, glutes, hamstrings; full body → none | **AI_COACH §2.2 table.** It lives in `services/muscles.py`, owned by the engineer. |
| C13 | Pages `/recovery`, `/strength` | n/a | Separate GET pages | **Cut.** Recovery appears on Today, and strength appears on the new `/progress`. |
| C14 | Prescription rule | +5/+10 lb simple rule | AI_COACH §4.5 rule table | **AI_COACH §4.5.** |
| C15 | Unit on set form | n/a | `unit` field on `SetLogForm` | The unit comes **server-side** from `TrainerSettings.unit` (default `lb`). It is not a form field. |
| C16 | Video on the library page | Thumbnails; the video moves to exercise detail | n/a | Accepted. `tests/test_youtube.py` is updated to assert on `/exercises/<id>`. This is an intentional behaviour change (section 7.3). |
| C17 | Tab bar | 4 tabs, Sync moves under Profile | n/a | Accepted: Today / Library / Progress / Profile. |

### 0.2 Cut from iteration 1 (do not build; keep the specs as "phase 2")

* Light-theme toggle and stored theme. Dark only, though the light tokens may sit in CSS unused.
* Bodyweight, population strength standards, Starter…Elite levels, and the "Units & bodyweight" row.
  A "Units" row (lb/kg + default minutes) stays.
* Strength-Index-over-time sparkline on Progress. The e1RM sparkline on exercise detail **stays**.
* Plan-level swaps, and swaps after sets are logged.
* `/recovery` and `/strength` standalone pages. Interactive body-map regions. A "user" muscle-edit UI.
* Weekly-goal setting. The streak is defined as consecutive ISO weeks with ≥ 1 gym day.
* "Mobility session" ghost button in the rest state.
* Native `<dialog>` sheets are **optional** enhancement. Every sheet must work as `<details>` or as a page.
* App icon recolour (optional asset work), prompt caching, Apple Watch per-set sync, warm-up sets,
  supersets, and multi-day programs.

---

## 1. Data model (owner: software engineer, `app/models.py`)

Only **new tables**. No new columns on existing tables, so `db.create_all()` upgrades an existing
`workout.db` in place and no "delete workout.db" note is needed. New relationships/backrefs on
existing models add no columns.

```python
class TrainerSettings(db.Model):              # lazily created; absence == defaults
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    unit = db.Column(db.String(2), nullable=False, default="lb")            # "lb" | "kg"
    default_minutes = db.Column(db.Integer, nullable=False, default=45)      # 10..120


class ExerciseMuscles(db.Model):              # canonical tags for one library exercise
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    exercise_id = db.Column(db.Integer, db.ForeignKey("exercise.id", ondelete="CASCADE"),
                            unique=True, nullable=False)
    primary = db.Column(db.String(12))                                       # canonical or NULL
    secondary_json = db.Column(db.Text, nullable=False, default="[]")        # list[str] canonical, <=4
    pattern = db.Column(db.String(20), nullable=False, default="other")
    source = db.Column(db.String(10), nullable=False, default="rules")       # rules | ai | user

    exercise = db.relationship(
        "Exercise", backref=db.backref("muscles_row", uselist=False, cascade="all, delete-orphan"))


class EntryPlan(db.Model):                    # targets + coach state for one SessionEntry
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("session_entry.id", ondelete="CASCADE"),
                         unique=True, nullable=False)
    targets_json = db.Column(db.Text, nullable=False, default="[]")  # list[PlannedSet] JSON, 1..20 items
    unit = db.Column(db.String(2), nullable=False, default="lb")
    rest_seconds = db.Column(db.Integer, nullable=False, default=90)  # 0..600
    rep_low = db.Column(db.Integer)                                   # NULL = timed
    rep_high = db.Column(db.Integer)
    pattern = db.Column(db.String(20), nullable=False, default="other")
    progression_rule = db.Column(db.String(12), nullable=False, default="no_history")
    progression_note = db.Column(db.String(160), nullable=False, default="")
    plan_index = db.Column(db.Integer)                                # index in DailyPlan, if from a plan
    swaps_json = db.Column(db.Text, nullable=False, default="[]")     # list[PlannedExercise] JSON, <=3
    swaps_source = db.Column(db.String(10))                           # "ai" | "fallback" | NULL

    entry = db.relationship(
        "SessionEntry", backref=db.backref("plan", uselist=False, cascade="all, delete-orphan"))


class SetLog(db.Model):                       # one row per COMPLETED set
    __table_args__ = (db.UniqueConstraint("entry_id", "set_index"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("session_entry.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    set_index = db.Column(db.Integer, nullable=False)        # 0-based, 0..19
    reps = db.Column(db.Integer)                             # 0..100; NULL for timed sets
    seconds = db.Column(db.Integer)                          # 1..600; timed sets only
    weight = db.Column(db.Float)                             # 0..2000 in `unit`; NULL = bodyweight
    unit = db.Column(db.String(2), nullable=False, default="lb")
    rating = db.Column(db.String(4))                         # "easy" | "ok" | "hard" | NULL
    target_reps = db.Column(db.Integer)                      # copied server-side from EntryPlan
    target_seconds = db.Column(db.Integer)
    target_weight = db.Column(db.Float)
    logged_at = db.Column(db.DateTime, nullable=False, default=utcnow)   # UTC

    entry = db.relationship(
        "SessionEntry",
        backref=db.backref("set_logs", cascade="all, delete-orphan", order_by="SetLog.set_index"))


class DailyPlan(db.Model):                    # today's plan (AI or fallback), validated JSON
    __table_args__ = (db.UniqueConstraint("user_id", "day"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    day = db.Column(db.Date, nullable=False)                 # user's timezone (today_for)
    source = db.Column(db.String(10), nullable=False)        # "ai" | "fallback"
    plan_json = db.Column(db.Text, nullable=False)           # TodayPlan.model_dump_json()
    generations = db.Column(db.Integer, nullable=False, default=0)   # AI generations today (max 5)
    session_id = db.Column(db.Integer, db.ForeignKey("workout_session.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class AIUsage(db.Model):                      # per-user daily AI budget + token accounting
    __table_args__ = (db.UniqueConstraint("user_id", "day"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    day = db.Column(db.Date, nullable=False)                 # UTC day
    calls = db.Column(db.Integer, nullable=False, default=0)
    input_tokens = db.Column(db.Integer, nullable=False, default=0)
    output_tokens = db.Column(db.Integer, nullable=False, default=0)
```

Rules:

* **Tracked vs legacy entries.** A `SessionEntry` with an `EntryPlan` is *tracked*. Its legacy
  summary fields are derived from `set_logs` by `sets.sync_entry()`:
  `sets_done = len(set_logs)`, `reps_done = str(last.reps)` or `f"{last.seconds}s"`
  (or the target text when there are no logs), and
  `weight = fmt_weight(last.weight, unit)` or `""`. An entry with no `EntryPlan` is *legacy*:
  its fields are shown as typed, and recovery and progression expand it by `sets_done`.
  Existing history, share pages and tests therefore keep working.
* SQLite does not enforce `ondelete`. All deletes go through the ORM (cascades above), plus:
  * `exercise_delete` also sets `SessionEntry.exercise_id = NULL` for the user's entries pointing at
    that exercise. SQLite can reuse rowids, and stale ids would corrupt progression history.
  * `session_delete` also sets `DailyPlan.session_id = NULL` for rows pointing at that session.
* **Account deletion order** (`auth/routes.delete_account`), all filtered by `user_id=uid`, before
  the existing session, exercise and user deletes:
  `AIUsage, DailyPlan, SetLog, EntryPlan, ExerciseMuscles, TrainerSettings, ShareLink, ApiToken,
  HealthSample, Integration, GymProfile`.
* `user_id` is duplicated on `SetLog` and `EntryPlan` on purpose, so `owned_or_404` works on them.
  Routes **also** check `row.entry.session_id == ws.id`.
* `Exercise.source` gains the value `"coach"` for exercises auto-added from the built-in catalog.
  It is a String(10) value, not a schema change.

---

## 2. Service modules (exact APIs)

Layer rule (ARCHITECTURE.md): services take plain arguments, use no `request` and no templates, and never
call `url_for`. Every time-dependent function takes `now: datetime | None = None` (naive UTC).
Nothing uses randomness.

Import graph (no cycles): `models` ← `muscles` ← `plan_schema` ← {`progression`, `recovery`,
`catalog`} ← {`strength`, `ai_coach`, `sets`, `summary`, `activity`} ← `trainer` ← routes.
`ai_coach` must **not** import `trainer`.

### 2.1 `app/services/muscles.py` (engineer). Single home of every shared constant.

It contains **exactly** AI_COACH.md §2 (constants, synonym table, name rules incl. precedence notes,
`DEFAULT_SECONDARY`, equipment keyword mapping), plus:

```python
MuscleGroup = Literal["chest","back","shoulders","biceps","triceps","forearms",
                      "core","quads","hamstrings","glutes","calves"]
CANONICAL: tuple[MuscleGroup, ...]          # the 11 keys, this order
LARGE: frozenset[str]                       # chest, back, quads, hamstrings, glutes
LABELS: dict[str, str]                      # "quads" -> "Quads"
Pattern = Literal[...]                      # AI_COACH §2.1
LOWER_COMPOUND: frozenset[str]
EquipmentKey = Literal[...]                 # == [k for k, _ in forms.EQUIPMENT]; a test asserts equality
RATINGS: tuple[str, ...] = ("easy", "ok", "hard")
RATING_LABELS = {"easy": "Easy", "ok": "Good", "hard": "Hard"}
UNITS = ("lb", "kg"); KG_TO_LB = 2.20462
FOCUS_MUSCLES: dict[str, list[MuscleGroup]] # keys == forms.FOCUS keys + "auto" -> []
SWAP_REASONS: tuple[tuple[str, str], ...] = (
    ("busy", "Station is busy"), ("no_equipment", "Don't have the equipment"),
    ("discomfort", "Feels uncomfortable"), ("too_hard", "Too hard today"),
    ("preference", "Just want something else"))

@dataclass(frozen=True)
class MuscleMap:
    primary: MuscleGroup | None
    secondary: tuple[MuscleGroup, ...] = ()
    pattern: Pattern = "other"
    @property
    def all(self) -> tuple[MuscleGroup, ...]: ...   # primary + secondary, canonical order

def normalize_group(text: str) -> list[MuscleGroup]
def infer_from_name(name: str) -> MuscleMap | None
def normalize_name(name: str) -> str        # lowercase, aliases (db→dumbbell…), collapsed spaces
def map_exercise(name: str, muscle_group: str) -> MuscleMap
def muscles_for(exercise: Exercise | None, name: str = "") -> MuscleMap   # uses ExerciseMuscles row if any
def ensure_muscles(exercise: Exercise, *, ai_map: MuscleMap | None = None) -> ExerciseMuscles
    # adds/updates the row (no commit). Never overwrites source="user". ai_map -> source="ai".
def equipment_keys(text: str) -> frozenset[EquipmentKey]
def available(gym_equipment: list[str]) -> frozenset[EquipmentKey]       # always adds "bodyweight"
def is_canonical(value: str) -> bool
```

`forms.py` keeps `EQUIPMENT`/`FOCUS`/`GOALS`/`LEVELS` (they are the UI choices). `muscles.py`
imports nothing from `forms.py`. A test ties the two together.

### 2.2 `app/services/plan_schema.py` (AI agent). Shared pydantic shapes.

```python
class PlannedSet(BaseModel):
    target_reps: int | None = Field(default=None, ge=1, le=50)
    target_seconds: int | None = Field(default=None, ge=5, le=600)
    weight: float | None = Field(default=None, ge=0, le=2000)     # in `unit`, rounded to plates
    unit: Literal["lb", "kg"] = "lb"
    weight_source: Literal["history", "e1rm", "ai", "none"] = "none"
    # validator: exactly one of target_reps / target_seconds

class PlannedExercise(BaseModel):            # = AI_COACH §6.6, unchanged
    name: str = Field(min_length=2, max_length=80)
    exercise_id: int | None = None
    primary_muscle: MuscleGroup
    secondary_muscles: list[MuscleGroup] = Field(default_factory=list, max_length=4)
    equipment: list[EquipmentKey] = Field(default_factory=list, max_length=4)
    movement_pattern: Pattern = "other"
    rest_seconds: int = Field(ge=0, le=600)
    sets: list[PlannedSet] = Field(min_length=1, max_length=10)
    form_cues: list[Annotated[str, StringConstraints(max_length=160)]] = Field(default_factory=list, max_length=4)
    progression_rule: str = Field(default="no_history", max_length=12)
    progression_note: str = Field(default="", max_length=160)
    recovery_warning: bool = False
    source: Literal["ai", "library", "catalog"] = "ai"

class TodayPlan(BaseModel):
    schema_version: Literal[1] = 1
    kind: Literal["workout", "recovery"] = "workout"             # NEW (C11)
    title: str = Field(max_length=100)
    focus_muscles: list[MuscleGroup] = Field(default_factory=list, max_length=6)
    estimated_minutes: int = Field(ge=5, le=180)
    rationale: str = Field(default="", max_length=400)
    exercises: list[PlannedExercise] = Field(min_length=1, max_length=10)
    source: Literal["ai", "fallback"]
    notice: str = Field(default="", max_length=200)

@dataclass(frozen=True)
class PlanRowView:                           # what the hero, summary and swap page render
    name: str
    sets: int
    reps_text: str          # "8", "8–12", "30s"  (en dash for ranges)
    weight: float | None    # display unit; None => "" (bodyweight / unknown)
    unit: str
    weight_label: str       # "", "suggested" (weight_source == "ai")
    note: str               # progression_note
    warning: bool           # recovery_warning
    primary: MuscleGroup
    delta: float | None = None   # used only by "Next time" rows

def plan_row_view(ex: PlannedExercise) -> PlanRowView
def load_targets(text: str) -> list[PlannedSet]           # safe: [] on any error
def dump_targets(targets: list[PlannedSet]) -> str
def load_exercises(text: str) -> list[PlannedExercise]    # safe: [] on any error (swaps_json)
def estimate_minutes(exercises: list[PlannedExercise]) -> int   # AI_COACH §6.6 step 7
```

### 2.3 `app/services/recovery.py` (engineer). Formula from AI_COACH §3, verbatim.

```python
Rating = Literal["easy", "ok", "hard"]
@dataclass(frozen=True) class WorkSet: session_id: int; performed_at: datetime; primary: MuscleGroup | None;
                                      secondary: tuple[MuscleGroup, ...]; rating: Rating | None = None
@dataclass(frozen=True) class MuscleRecovery: muscle; recovery: int; status: Literal["fresh","recovering","fatigued"];
                                             last_trained_at: datetime | None; hours_since: float | None;
                                             sets_7d: float; ready_at: datetime | None
RecoveryMap = dict[MuscleGroup, MuscleRecovery]          # always 11 keys, canonical order
LOOKBACK = timedelta(days=7); FRESH, RECOVERING = 85, 50

def compute_recovery(sets: Iterable[WorkSet], now: datetime) -> RecoveryMap          # pure
def load_work_sets(user_id: int, since: datetime) -> list[WorkSet]                   # DB (tracked + legacy)
def recovery_map(user_id: int, now: datetime | None = None) -> RecoveryMap
def neglected_muscles(user_id: int, now: datetime | None = None) -> list[MuscleGroup]  # ≤3, AI_COACH §3.5

def rec_class(m: MuscleRecovery) -> str
    # "rec-na" if m.last_trained_at is None
    # >=85 "rec-4" (Fresh) | 70-84 "rec-3" | 50-69 "rec-2" (Recovering) | 25-49 "rec-1" | <25 "rec-0" (Fatigued)

@dataclass(frozen=True)
class RecoveryView:
    muscle: MuscleGroup; label: str; pct: int; status: str     # "fresh" | "recovering" | "fatigued"
    status_label: str        # "Fresh" | "Recovering" | "Fatigued"
    rec: str                 # rec-* class (fixed vocabulary only)
    trained: bool            # last_trained_at is not None
    ready_in_h: int | None   # ceil hours until ready_at, None if fresh

@dataclass(frozen=True)
class RecoveryPanel:
    items: list[RecoveryView]                    # 11, canonical order
    by_muscle: dict[str, RecoveryView]
    groups: dict[str, list[RecoveryView]]        # keys "fresh","recovering","fatigued" (in that order), each sorted by pct asc
    desc: str                                    # "Chest 49% recovered, fatigued. Back 100%…" for <desc>
    has_history: bool                            # any muscle trained in the lookback

def recovery_panel(rmap: RecoveryMap) -> RecoveryPanel
```

### 2.4 `app/services/progression.py` (AI agent). AI_COACH §4 verbatim, plus loaders.

```python
KG_TO_LB (re-exported from muscles)
@dataclass(frozen=True) class ParsedWeight: value: float | None; unit: Literal["lb","kg"]; bodyweight: bool
def parse_weight(text: str, default_unit: str = "lb") -> ParsedWeight
def parse_reps(text: str) -> int | None
def parse_seconds(text: str) -> int | None
def parse_rep_range(text: str) -> tuple[int, int] | None
def to_lb(value: float, unit: str) -> float
def from_lb(value_lb: float, unit: str) -> float
def fmt_weight(value: float | None, unit: str) -> str        # 135.0 -> "135 lb", 62.5 -> "62.5 kg", None -> ""

@dataclass(frozen=True) class PerformedSet: reps; seconds; weight_lb; rating; target_reps; target_seconds
@dataclass(frozen=True) class Exposure: session_id: int; performed_at: datetime; sets: tuple[PerformedSet, ...]

def exercise_key(exercise_id: int | None, name: str) -> str
def load_history(user_id: int, key: str, *, now: datetime, days: int = 120, limit: int = 4) -> list[Exposure]
    # newest first; skips the user's open session; legacy entries expand by sets_done
def load_all_history(user_id: int, *, now: datetime, days: int = 180
                     ) -> tuple[dict[str, list[Exposure]], dict[str, str], dict[str, int | None]]
    # (exposures_by_key newest-first, display name by key, exercise_id by key) — feeds strength.py

def epley(weight_lb: float, reps: int) -> float | None
def exposure_e1rm(exp: Exposure) -> float | None
def weight_for_reps(e1rm_lb: float, reps: int, rir: int = 2) -> float
INCREMENT_LB, INCREMENT_KG
def increment(equipment: frozenset[EquipmentKey], unit: str) -> float
def round_load(value: float, inc: float, mode: Literal["nearest", "down"] = "nearest") -> float

@dataclass(frozen=True) class Prescription: sets; rep_range; seconds=None; pattern="other"; equipment=frozenset()
@dataclass(frozen=True) class SetTarget: set_index; reps; seconds; weight; unit
@dataclass(frozen=True) class TargetResult: targets; rule; reason; e1rm_lb; source
def next_targets(history: list[Exposure], rx: Prescription, *, unit: str, now: datetime) -> TargetResult
def to_planned_sets(result: TargetResult) -> list[PlannedSet]   # weight_source from result.source
```

### 2.5 `app/services/strength.py` (engineer). Formula from AI_COACH §5, verbatim.

```python
@dataclass(frozen=True) class LiftScore: key: str; exercise_id: int | None; name: str; muscle: MuscleGroup;
    baseline_e1rm_lb: float; current_e1rm_lb: float; index: int; stale: bool; pr: bool;
    best_set: str              # "185 lb × 8" in the user's unit
    current_e1rm: float        # display unit, rounded to 1 decimal
@dataclass(frozen=True) class MuscleScore: muscle; index: int; delta_28d: int | None; lifts: int
@dataclass(frozen=True) class StrengthReport: overall: int | None; overall_delta_28d: int | None;
    muscles: dict[MuscleGroup, MuscleScore]; lifts: list[LiftScore]

def compute_strength(exposures_by_key, names, muscles, *, as_of: datetime,
                     exercise_ids: dict[str, int | None] | None = None, unit: str = "lb") -> StrengthReport   # pure
def strength_report(user_id: int, *, now: datetime | None = None, unit: str = "lb") -> StrengthReport

def index_pct(index: int | None) -> int        # clamp(round(index * 2 / 3), 0, 100); None -> 0
@dataclass(frozen=True) class StrengthRow: muscle; label; index: int | None; pct: int; delta_28d: int | None; lifts: int
def strength_rows(report: StrengthReport) -> list[StrengthRow]          # all 11 groups, canonical order
def sparkline_points(values: list[float], *, width: int = 100, height: int = 32, pad: int = 2) -> str | None
    # "x,y x,y …" with 1-decimal numbers only; None if < 2 values. Oldest -> newest left -> right.
```

### 2.6 `app/services/sets.py` (engineer). Set logging and entry views.

```python
@dataclass(frozen=True)
class SetRowView:
    set_index: int; number: int                     # number = set_index + 1
    target_reps: int | None; target_seconds: int | None; target_weight: float | None
    target_text: str                                # "135 × 10", "BW × 12", "30s", "— × 8"
    log: SetLog | None                              # None = not done yet
    weight_value: str                               # prefill: log.weight or target weight, "" if None
    reps_value: str                                 # prefill: log.reps or target reps
    seconds_value: str
    is_done: bool; is_current: bool                 # current = first not-done row of the current entry
    timed: bool

@dataclass(frozen=True)
class EntryView:
    entry: SessionEntry
    exercise: Exercise | None          # library row (owned) or None if deleted
    tracked: bool                      # has EntryPlan
    rows: list[SetRowView]             # [] for legacy entries
    done: int; planned: int
    rest_seconds: int
    note: str                          # progression_note
    weight_label: str                  # "suggested" when targets came from AI weight, else ""
    muscles: MuscleMap
    is_current: bool                   # first tracked entry with done < planned
    is_complete: bool                  # done >= planned
    can_swap: bool                     # tracked and done == 0 and session active

def entry_views(ws: WorkoutSession, *, library: dict[int, Exercise]) -> list[EntryView]
def log_set(entry: SessionEntry, *, set_index: int, reps: int | None, seconds: int | None,
            weight: float | None, rating: str | None, unit: str) -> SetLog
    # upsert by (entry_id, set_index); copies target_* from EntryPlan.targets[set_index] (if any); sync_entry
def delete_set(log: SetLog) -> None                 # then sync_entry
def rate_set(log: SetLog, rating: str | None) -> None
def add_set(entry: SessionEntry) -> None            # appends a copy of the last target (max 20)
def sync_entry(entry: SessionEntry) -> None          # legacy summary fields (section 1)
def unlogged_sets(ws: WorkoutSession) -> int          # Σ planned - done over tracked entries
def progress(ws: WorkoutSession) -> tuple[int, int]   # (exercises complete, tracked exercises)
```

None of these commit. Routes commit.

### 2.7 `app/services/summary.py` (engineer). Finished-workout summary.

```python
@dataclass(frozen=True) class PRRow: name: str; exercise_id: int | None; e1rm: float; delta: float | None; best_set: str
@dataclass(frozen=True)
class WorkoutSummary:
    minutes: int | None           # (ended_at - started_at) // 60
    sets_done: int                # Σ logged sets (+ legacy sets_done)
    volume: int                   # Σ weight × reps over logged sets with weight, display unit, rounded
    unit: str
    ratings: dict[str, int]       # {"easy": n, "ok": n, "hard": n}
    rated_total: int
    prs: list[PRRow]              # exposures in this session whose e1RM beats all earlier ones
    muscles_worked: tuple[MuscleGroup, ...]   # union of primary + secondary, canonical order
    next_time: list[PlanRowView]  # next_targets per tracked entry; delta = target weight − working weight
def workout_summary(ws: WorkoutSession, *, unit: str, now: datetime | None = None) -> WorkoutSummary
```

### 2.8 `app/services/activity.py` (engineer). Week strip, calendar, streak.

```python
@dataclass(frozen=True)
class DayCell:
    day: date; dow: str                 # "M","T","W","T","F","S","S"
    in_month: bool; is_gym: bool; is_today: bool; is_future: bool
    kcal: int | None                    # Σ session calories that day
    aria: str                           # "Oct 14, gym day, 312 kcal" | "Oct 15, no gym" | "Oct 30"
@dataclass(frozen=True)
class MonthView:
    year: int; month: int; title: str   # "October 2026"
    weeks: list[list[DayCell]]          # Monday-first, padded with in_month=False cells
    gym_days: int
    prev: str                           # "2026-09"
    next: str | None                    # None if that month is in the future
def week_strip(user, today: date) -> list[DayCell]                      # Mon..Sun of today's ISO week
def month_calendar(user, year: int, month: int, today: date) -> MonthView  # ≤ 2 queries
def streak_weeks(user, today: date) -> int
    # consecutive ISO weeks with ≥1 gym day, ending this week (or last week if this week has none yet)
```

"Gym day" uses the same rule as `DaySummary.went_to_gym`: a session finished, or one with entries.

### 2.9 `app/services/prefs.py` (engineer)

```python
def unit_for(user_id: int) -> Literal["lb", "kg"]          # read-only; "lb" if no row
def default_minutes_for(user_id: int) -> int               # read-only; 45 if no row
def get_or_create_settings(user_id: int) -> TrainerSettings  # POST paths only (adds, no commit)
```

### 2.10 `app/services/catalog.py` (AI agent)

`CatalogExercise` and `CATALOG` per AI_COACH §8 (≥ 4 per group, ≥ 1 bodyweight each, our own names
and cues), plus `def doable(item: CatalogExercise, equipment: frozenset[EquipmentKey]) -> bool` and
`def find(name: str) -> CatalogExercise | None` (matched by normalized name).

### 2.11 `app/services/ai_coach.py` (AI agent). Extended, backwards compatible.

```python
class AIError(Exception)                                 # unchanged
GeneratedExercise, GeneratedPlan, OUTPUT_SCHEMA, SYSTEM_PROMPT, build_prompt   # unchanged
def generate_workout(*, user_id: int | None = None, **profile) -> GeneratedPlan
    # same kwargs to the client as today (existing tests assert them); budget + usage only when user_id given
def _call_structured(*, system: str, prompt: str, schema: dict, effort: str, max_tokens: int,
                     user_id: int | None = None) -> dict       # AI_COACH §6.5
def _check_budget(user_id: int | None) -> None                 # raises AIError when AIUsage.calls >= AI_DAILY_CALLS
def remaining_calls(user_id: int) -> int
TODAY_SCHEMA, SWAP_SCHEMA, TODAY_SYSTEM_PROMPT, SWAP_SYSTEM_PROMPT
class AISet, AIExercise, AITodayPlan, AISwaps                   # AI_COACH §6.4 / §7.1
def build_today_prompt(ctx: TrainerContext) -> str
def build_swap_prompt(exercise: PlannedExercise, ctx: TrainerContext, *, reason: str, exclude: list[str]) -> str
def generate_today(ctx: TrainerContext) -> AITodayPlan
def suggest_swaps(exercise: PlannedExercise, ctx: TrainerContext, *, reason: str, exclude: list[str]) -> list[AIExercise]
def _clean(text: str, limit: int) -> str
```

`TrainerContext` and its helper dataclasses (`LibraryItem`, `RecentExercise`, `RecentSession`) are
defined in **`plan_schema.py`**, which avoids an ai_coach → trainer import. Their fields are those in AI_COACH §6.1.
Usage accounting uses `getattr(response, "usage", None)` with a default of 0, and `getattr(response,
"_request_id", None)`, so the existing `FakeMessages` keeps working. **Never log prompt or response
content.**

### 2.12 `app/services/trainer.py` (AI agent). Orchestration.

```python
def build_context(user: User, *, now: datetime | None = None, minutes: int | None = None,
                  focus: str = "auto", extra: str = "") -> TrainerContext
def fallback_plan(ctx: TrainerContext) -> TodayPlan                    # AI_COACH §8; sets kind="recovery" on recovery day
def finalize_plan(ai: AITodayPlan, ctx: TrainerContext) -> TodayPlan   # AI_COACH §6.6
def today_plan(user: User, *, now: datetime | None = None) -> tuple[TodayPlan, DailyPlan | None]
    # GET path. Stored valid plan for today, else UNSAVED fallback. Never calls the AI. No writes.
def generate_plan(user: User, *, minutes: int, focus: str, extra: str, use_ai: bool = True,
                  now: datetime | None = None) -> tuple[TodayPlan, DailyPlan]
    # POST path. Upserts DailyPlan (no commit). AI only if key set, budget left and generations < 5;
    # otherwise fallback with notice. Refuses to replace a plan whose session_id is set → returns it unchanged.
def start_plan(user: User, *, now: datetime | None = None) -> WorkoutSession
    # Reuses an open session if one exists (returns it, no changes). Else: persists today's plan
    # (fallback if none stored), creates WorkoutSession(day=today_for), one SessionEntry + EntryPlan per
    # planned exercise (in order; plan_index set), adds unknown exercises to the library
    # (source "ai" or "coach", ensure_muscles), writes exercise_ids back into plan_json, links session_id.
def attach_targets(user: User, entry: SessionEntry, exercise: Exercise, *, sets: int,
                   reps_text: str, weight_text: str, now: datetime | None = None) -> EntryPlan
    # For "Add exercise" mid-session: targets from next_targets (history wins); else from the
    # typed reps/weight; else from exercise.reps. Rest from exercise.rest_seconds.
def planned_from_entry(entry: SessionEntry) -> PlannedExercise
def swap_options(user: User, entry: SessionEntry, *, reason: str, use_ai: bool,
                 now: datetime | None = None) -> tuple[list[PlannedExercise], str, str]
    # (options ≤3, source "ai"|"fallback", notice). Stores into EntryPlan.swaps_json/swaps_source (no commit).
def fallback_swaps(exercise: PlannedExercise, ctx: TrainerContext, *, reason: str,
                   exclude: list[str], n: int = 3) -> list[PlannedExercise]
def apply_swap(user: User, entry: SessionEntry, choice: int) -> SessionEntry
    # bounds-checked against stored swaps (IndexError -> caller 404); requires 0 set_logs;
    # resolves/creates the library Exercise, rewrites entry.exercise_id/name and EntryPlan targets,
    # clears swaps_json.
```

`exercise_id` values read from stored JSON go through an ownership check
(`Exercise.user_id == user.id`, else treated as `None`). Services cannot `abort`. Routes use
`owned_or_404` for ids that arrive in URLs.

---

## 3. Config, forms, filters, context (owner: software engineer)

### 3.1 Config (`app/config.py`, `.env.example`)

`AI_DAILY_CALLS = int(os.environ.get("AI_DAILY_CALLS", "25"))`, `AI_MAX_PLAN_GENERATIONS = 5`.
`AI_MODEL` is unchanged.

### 3.2 Forms (`app/forms.py`)

```python
class AIGenerateForm(FlaskForm):         # focus becomes RadioField(choices=FOCUS, default="full body"); rest unchanged
class EntryForm(FlaskForm):              # unchanged fields + log_all = BooleanField("Already done — log all sets")
class PlanGenerateForm(FlaskForm):
    minutes = IntegerField(default=45, validators=[InputRequired(), NumberRange(10, 120)])
    focus = RadioField(choices=[("auto", "Let the coach decide")] + FOCUS, default="auto")
    extra = StringField(validators=[Optional(), Length(max=300)])
class SetLogForm(FlaskForm):
    set_index = IntegerField(validators=[InputRequired(), NumberRange(0, 19)])   # InputRequired: 0 is valid
    reps = IntegerField(validators=[Optional(), NumberRange(0, 100)])
    seconds = IntegerField(validators=[Optional(), NumberRange(1, 600)])
    weight = DecimalField(places=2, validators=[Optional(), NumberRange(0, 2000)])
    rating = SelectField(choices=[("", "—"), ("easy", "Easy"), ("ok", "Good"), ("hard", "Hard")],
                         validators=[Optional()], default="")
    # route rule: blank reps AND seconds -> fall back to the row's target; still none -> error
class RateForm(FlaskForm):
    rating = SelectField(choices=[("", "—"), ("easy", "Easy"), ("ok", "Good"), ("hard", "Hard")], validators=[Optional()])
class SwapForm(FlaskForm):
    reason = RadioField(choices=list(SWAP_REASONS), default="preference")
class TrainerSettingsForm(FlaskForm):
    unit = SelectField(choices=[("lb", "lb"), ("kg", "kg")])
    default_minutes = IntegerField(default=45, validators=[InputRequired(), NumberRange(10, 120)])
```

### 3.3 Template filters and globals (`app/__init__.py`)

| Name | Kind | Behaviour |
|---|---|---|
| `localtime` | filter | existing |
| `weight(value, unit)` | filter | `progression.fmt_weight` (`""` for None) |
| `num(value)` | filter | int with thousands separator (`9,840`); `"—"` for None |
| `pct(value)` | filter | `max(0, min(100, int(value or 0)))` |
| `iso_utc(dt)` | filter | `"2026-10-21T07:02:00Z"` (for `data-elapsed-since`) |
| `muscle_label(key)` | filter | `LABELS.get(key, "")` |
| context `now_session` | global | the user's open `WorkoutSession` or None (authenticated only) |
| context `now_session_title` | global | linked DailyPlan title, or "Workout" |
| context `integration_alert` | global | True if the user's Oura `Integration.last_error` is set |
| context `user_unit` | global | `prefs.unit_for(current_user.id)`, or "lb" when anonymous |
| context `canonical_muscles` | global | `CANONICAL` (for chips and filters) |
| existing | global | `google_enabled`, `oura_enabled`, `ai_enabled` |

The context processor must cost at most 2 queries and must work for anonymous users and error pages.

### 3.4 Per-user rate-limit key

Add `def user_or_ip() -> str` in `app/extensions.py`. It returns `f"user:{current_user.id}"` when
authenticated, else the remote address. Use it via `@limiter.limit("…", key_func=user_or_ip)` on the AI and set routes.

---

## 4. Routes and template contracts

All routes are `@login_required` unless stated otherwise. Every state change is POST with CSRF. Any id in a URL
is loaded with `owned_or_404`. Child rows are also checked (`entry.session_id == ws.id`,
`log.entry.session_id == ws.id`), and a mismatch gives **404**.

### 4.1 Route table

| URL | Method | Endpoint | Form | Limit (key) | Result |
|---|---|---|---|---|---|
| `/` | GET | `workouts.today` | `PlanGenerateForm` (render only) | default | `today.html` |
| `/history` | GET | `workouts.history` | n/a | default | `history.html`; `?day=YYYY-MM-DD` (≤ today, else ignored) shows one day |
| `/progress` | GET | `workouts.progress` **new** | n/a | default | `progress.html`; `?month=YYYY-MM` (2000–2100, not future, else current) |
| `/gym` | GET/POST | `workouts.gym_profile` | `GymProfileForm` | default | unchanged |
| `/exercises` | GET | `workouts.exercises` | n/a | default | `exercises.html`; `?muscle=<canonical>` filters (invalid is ignored) |
| `/exercises/<id>` | GET | `workouts.exercise_detail` **new** | n/a | default | `exercise_detail.html` |
| `/exercises/new` | GET/POST | `workouts.exercise_new` | `ExerciseForm` | default | + `ensure_muscles`; redirect to `exercises` (unchanged) |
| `/exercises/<id>/edit` | GET/POST | `workouts.exercise_edit` | `ExerciseForm` | default | + `ensure_muscles`; redirect to **`exercise_detail`** |
| `/exercises/<id>/delete` | POST | `workouts.exercise_delete` | n/a | default | + null `SessionEntry.exercise_id`s (section 1) |
| `/exercises/ai` | GET/POST | `workouts.exercise_ai` | `AIGenerateForm` | 10/h (IP, unchanged) | passes `user_id=current_user.id` |
| `/exercises/ai/save` | POST | `workouts.exercise_ai_save` | raw lists | default | + `ensure_muscles` (rules) per saved row |
| `/plan/generate` | POST | `workouts.plan_generate` **new** | `PlanGenerateForm` | `6 per hour;20 per day` (user) | `generate_plan(use_ai=True)`; flash notice; → `today` |
| `/plan/rebuild` | POST | `workouts.plan_rebuild` **new** | `PlanGenerateForm` | `60 per hour` (user) | `generate_plan(use_ai=False)`; → `today` |
| `/plan/start` | POST | `workouts.plan_start` **new** | n/a | default | `start_plan` → `session_view` |
| `/sessions/start` | POST | `workouts.session_start` | n/a | default | unchanged (empty workout) |
| `/sessions/<sid>` | GET/POST | `workouts.session_view` | `EntryForm` | default | POST creates entry + `attach_targets`; `log_all` → logs every target set |
| `/sessions/<sid>/entries/<eid>/delete` | POST | `workouts.entry_delete` | n/a | default | unchanged |
| `/sessions/<sid>/entries/<eid>/sets` | POST | `workouts.set_log` **new** | `SetLogForm` | `600 per hour` (user) | JSON or 302 to `session_view#ex-<eid>` |
| `/sessions/<sid>/entries/<eid>/sets/add` | POST | `workouts.set_add` **new** | n/a | default | 302 `#ex-<eid>` |
| `/sessions/<sid>/sets/<set_id>/delete` | POST | `workouts.set_delete` **new** | n/a | default | JSON or 302 |
| `/sessions/<sid>/sets/<set_id>/rate` | POST | `workouts.set_rate` **new** | `RateForm` | default | JSON or 302 |
| `/sessions/<sid>/entries/<eid>/swap` | GET | `workouts.entry_swap` **new** | `SwapForm` | default | `swap.html`. If there are no stored options, computes **fallback** options (no AI) and stores them |
| `/sessions/<sid>/entries/<eid>/swap` | POST | `workouts.entry_swap` | `SwapForm` | `30 per hour` (user) | `swap_options(use_ai=ai_enabled)`; PRG → GET |
| `/sessions/<sid>/entries/<eid>/swap/<int:choice>` | POST | `workouts.entry_swap_apply` **new** | n/a | default | `apply_swap`; bad `choice` or logged sets → 404 |
| `/sessions/<sid>/finish` · `/refresh` · `/calories` · `/delete` | POST | existing | existing | existing | unchanged (delete also nulls `DailyPlan.session_id`) |
| `/settings` | GET/POST | `auth.settings` | `SettingsForm` | default | richer context (4.2) |
| `/settings/trainer` | POST | `auth.trainer_settings` **new** | `TrainerSettingsForm` | default | → `auth.settings` |
| all other existing routes | n/a | unchanged | n/a | n/a | unchanged |

Set routes accept only **active** sessions (`ended_at is None`). On a finished session they return a 400 JSON
or a flash with a redirect. **JSON mode** is used if and only if the request header is `X-Requested-With: fetch`:

```json
// 200, set_log / set_delete / set_rate
{"ok": true, "set_id": 41 /* null after delete */, "set_index": 1, "is_done": true,
 "rating": "ok" /* or null */, "entry_done": 2, "entry_planned": 4,
 "exercises_done": 1, "exercises_total": 6, "unlogged_sets": 13, "rest_seconds": 120,
 "action": "/sessions/7/sets/41/delete" /* next action for this row's check button */,
 "rate_action": "/sessions/7/sets/41/rate" /* null after delete */}
// 400
{"ok": false, "error": "Enter reps for this set."}
```

### 4.2 Template context (exact variable names)

**Global** (every template): `current_user`, `request`, `google_enabled`, `oura_enabled`,
`ai_enabled`, `now_session`, `now_session_title`, `integration_alert`, `user_unit`,
`canonical_muscles`, `csrf_token()`, plus the filters in 3.3.

| Template | Variables |
|---|---|
| `base.html` | globals; blocks `title`, `head`, `body_class`, `content`, `tabbar` (overridable), `scripts` |
| `workouts/today.html` | `summary: DaySummary`, `active: WorkoutSession \| None` (kept), `hero: HeroView`, `recovery: RecoveryPanel`, `week: list[DayCell]`, `streak: int`, `plan_form: PlanGenerateForm`, `ai_left: int \| None` (AI generations left today; None if AI off), `unit: str` |
| `workouts/session.html` | `ws`, `form: EntryForm`, `library: dict[int, Exercise]`, `cal_form`, `apple_shortcut_url` (all kept) + `entries: list[EntryView]`, `plan_title: str \| None`, `progress: tuple[int, int]`, `unlogged: int`, `unit: str`, `summary: WorkoutSummary \| None` (only when ended), `focus_mode: bool` (= active) |
| `workouts/swap.html` **new** | `ws`, `entry: SessionEntry`, `current: PlanRowView`, `form: SwapForm`, `options: list[PlanRowView]` (index = choice), `source: str \| None`, `notice: str` |
| `workouts/history.html` | `days: list[DaySummary]` (kept), `single_day: bool`. Heading stays **"Last 14 days"** when not single |
| `workouts/progress.html` **new** | `report: StrengthReport`, `rows: list[StrengthRow]`, `overall_pct: int`, `top_lifts: list[LiftScore]` (≤ 5, by current e1RM), `month: MonthView`, `streak: int`, `unit: str` |
| `workouts/exercises.html` | `exercises: list[Exercise]` (kept), `search_url` (kept) + `groups: list[tuple[str, str, list[Exercise]]]` (muscle key or `"other"`, label, rows; canonical order, "Other" last), `muscle_filter: str \| None`, `muscle_of: dict[int, MuscleMap]` |
| `workouts/exercise_detail.html` **new** | `exercise`, `muscles: MuscleMap`, `targets: tuple[str, ...]` (= muscles.all), `e1rm: float \| None` (display unit), `best_set: str \| None`, `last_day: date \| None`, `spark_points: str \| None`, `history_rows: list[HistoryRow]`, `search_url`, `unit` |
| `workouts/exercise_form.html` | `form`, `title`, `exercise?` (unchanged) |
| `workouts/gym.html` | `form` (unchanged) |
| `workouts/ai_form.html` | `form`, `gym` (unchanged; `focus` is now a RadioField) |
| `workouts/ai_preview.html` | `plan`, `focus` (unchanged; **keep the hidden input names and `selected` values**) |
| `auth/login.html`, `auth/register.html` | `form` (unchanged) |
| `auth/settings.html` | `form`, `pw_form` (kept) + `trainer_form: TrainerSettingsForm`, `gym: GymProfile \| None`, `apple_tokens: int`, `apple_last_used: datetime \| None`, `oura: Integration \| None`, `share_links: int` |
| `integrations/index.html` | unchanged |
| `sharing/created.html`, `manage.html`, `public.html` | unchanged |
| `errors/error.html` | `code`, `title`, `message` (unchanged) |

Route-local view shapes (defined in `app/workouts/routes.py`, engineer):

```python
@dataclass(frozen=True)
class HeroView:
    state: Literal["plan", "active", "done", "rest"]
    title: str
    plan: TodayPlan | None             # plan/rest: today's plan; active: linked plan if any
    plan_saved: bool
    session: WorkoutSession | None     # active: open session; done: latest finished today
    rows: list[PlanRowView]            # first 3 plan rows (plan/rest)
    more: int                          # remaining plan rows
    total_sets: int
    focus: list[RecoveryView]          # plan.focus_muscles mapped through RecoveryPanel.by_muscle
    exercises_done: int; exercises_total: int      # active
    done_kcal: int | None; done_minutes: int | None # done

@dataclass(frozen=True)
class HistoryRow:
    day: date; session_id: int
    sets: list[str]                    # ["135 × 10", "145 × 8"] or legacy ["3 × 10 @ 135 lb"]
    e1rm: float | None                 # display unit
```

Hero state rules, evaluated in order:
1. `active` if `now_session` exists, on any day.
2. `done` if a finished session exists today **and** (there is no stored plan **or** the stored plan's
   `session_id` is set). Primary: "View summary". Ghost: "Start another" (`session_start`).
3. `rest` if `plan.kind == "recovery"`. Primary: "Start anyway" (`plan_start`, secondary styling).
4. `plan` otherwise. Primary: "Start workout" (`plan_start`). Secondary panel: `<details>` with
   `plan_form` and two submit buttons: "Build with AI coach" (`formaction` = `plan_generate`,
   only if `ai_enabled`, `data-busy="Planning your session…"`) and "Rebuild without AI"
   (`formaction` = `plan_rebuild`). Ghost "Start empty workout" (`session_start`).

### 4.3 Strings that tests depend on (designer must keep them)

`Last 14 days`, `No gym` (history) · `No wearable calories yet`, `active calories` (flash text) ·
`trained today`, `didn't go to the gym` (public page; it must not contain the lowercase owner
username anywhere) · `That username is taken`, `Wrong username or password.` · `id="share-url" value="…"`
**in that attribute order** (created.html) · hidden CSRF inputs written as
`name="csrf_token" value=` or via `hidden_tag()` · the AI preview hidden inputs and `selected` checkboxes ·
`youtube.com/results?search_query=how+to+do+…` and the `youtube-nocookie.com/embed/<id>` iframe on
**exercise detail** · escaped exercise names on `/exercises`.

### 4.4 Templates: new and changed (designer)

New: `_icons.html` (`icon(name, cls="")`), `_ui.html` (`stat_tile`, `chip`, `ring`, `bar`, `body_map`,
`set_row`, `plan_row`, `week_strip`, `calendar`, `page_head`), `workouts/progress.html`,
`workouts/exercise_detail.html`, `workouts/swap.html`.
Changed: **every** existing template (restyle, icons replace emoji, 4-tab bar, toasts, Now bar).
`_macros.html` keeps `field`, `post_button`, `video`, `share_button` with the same signatures.

---

## 5. Static assets and JS behaviours (designer)

Files: `static/css/app.css` (tokens, components, `.rec-*`, `.w-0…w-100` if needed),
`static/js/app.js` (global: confirm, busy, copy, share, tz, toasts, elapsed, stepper, filter, sheets),
`static/js/workout.js` **new** (set check-off, rating, rest timer, progress), loaded only from
`session.html` through `{% block scripts %}` with `<script src=… defer>`. Also update
`manifest.webmanifest` colours to `#0A0A0B` and set `theme-color` to match. Do not add webfonts or external hosts.

Hard rules: no inline `<script>`, no `<style>`, no `style=""`, no `on*=` attributes. Dynamic sizes go
through SVG attributes (`width`, `stroke-dashoffset`, `points`), with values clamped server-side or
through `|pct`. JS updates use `textContent`, `classList` and `setAttribute`, and never `innerHTML` with server data.
`fetch` is same-origin only.

| Hook | Behaviour | Without JS |
|---|---|---|
| `form[data-set-form]` | On submit: `fetch(form.action, {method:"POST", body:new FormData(form), headers:{"X-Requested-With":"fetch"}, credentials:"same-origin"})`. On `ok`: toggle `.is-done`, set `aria-pressed`, set `form.action = json.action`, update the rating form action, update progress counters (`[data-progress]`, `aria-live="polite"`), move `.is-current`, start the rest timer with `json.rest_seconds` (only when `is_done`). On a non-JSON or failed response: `form.submit()` | Normal POST → 302 to `#ex-<eid>` |
| `form[data-rate-form]` | Radio change → fetch → mark the chip selected | Radios plus a visible "Save" submit button (JS hides the button by adding a class; `<noscript>` is not used) |
| `[data-rest-timer]` (`hidden` in HTML) | Fixed bar: SVG ring (`stroke-dashoffset` via `setAttribute`), `−15` / `+15` / `Skip`, `navigator.vibrate(200)` at 0, live announcements only at 30 s, 10 s and 0, collapses 3 s after 0 | Stays hidden |
| `[data-elapsed-since]` | Ticks `m:ss` / `h:mm:ss` every second | Shows the server-rendered start time |
| `[data-stepper]` | ± buttons within the input's `min`/`max` | Plain number input |
| `[data-filter]` | Filters `[data-name]` rows in the target list | Muscle chips are server links (`?muscle=`) |
| `[data-sheet-open]` | Optional `dialog.showModal()` | `<details>` or a link to a full page |
| `.toast` | Success/info auto-dismiss after 4 s; errors stay | Stays in flow |
| `data-confirm`, `data-busy`, `data-copy`, `data-share-url`, `data-use-device-tz` | existing | existing |

Finish button: `data-confirm` is present only when `unlogged > 0`
("{n} sets not logged. Finish anyway?").

---

## 6. File ownership (parallel work, no shared edits)

| Owner | Files (create or edit) |
|---|---|
| **Software engineer** | `app/models.py`, `app/forms.py`, `app/config.py`, `app/extensions.py`, `app/__init__.py`, `app/security.py` (no change expected), `app/workouts/routes.py`, `app/auth/routes.py`, `app/integrations/routes.py`, `app/sharing/routes.py`, `app/services/muscles.py`, `app/services/recovery.py`, `app/services/strength.py`, `app/services/sets.py`, `app/services/summary.py`, `app/services/activity.py`, `app/services/prefs.py`, `app/services/calories.py`, `.env.example`, `README.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, tests: **every existing test file except `test_ai.py`**, plus new `test_muscles.py`, `test_recovery.py`, `test_strength.py`, `test_sets.py`, `test_today_progress.py`, `test_swap_routes.py`, `test_account_deletion.py`, `test_templates_csp.py` |
| **AI agent** | `app/services/ai_coach.py`, `app/services/progression.py`, `app/services/plan_schema.py` *(new; ownership added)*, `app/services/trainer.py` *(new; ownership added)*, `app/services/catalog.py` *(new; ownership added)*, `tests/test_ai.py`, `tests/test_progression.py`, `tests/test_trainer.py`, `docs/AI_COACH.md` |
| **Web designer** | `app/templates/**`, `app/static/**` (css, js, icons, manifest), `docs/DESIGN.md` |
| **Tech lead** | `docs/BUILD_CONTRACT.md` (read-only for everyone else) |

Ownership changes and the reasons for them:
* `trainer.py`, `catalog.py` and `plan_schema.py` go to the AI agent. They hold the coaching logic
  of AI_COACH §6–8 and are new files nobody else edits.
* `muscles.py` (shared constants and taxonomy) goes to the engineer, as instructed. The AI agent imports it and
  must not redefine `CANONICAL`, `LABELS`, `Pattern`, `EquipmentKey`, `RATINGS`, `FOCUS_MUSCLES` or
  `SWAP_REASONS`.
* `recovery.py` and `strength.py` go to the engineer, but they implement the AI_COACH §3 and §5 formulas **verbatim**.
  The AI agent reviews them but does not edit them.
* `test_youtube.py`, `test_sessions_and_calories.py` and the other existing tests go to the engineer.

**Sequencing:** Phase 0 is the engineer landing `models.py`, `muscles.py`, the `forms.py` additions,
`prefs.py` and the filter/context registration first, since everything imports them. Meanwhile the AI agent writes the pure parts
(`progression` parsing/epley/next_targets, `catalog`, `plan_schema`) and the designer writes tokens, icons
and base layout. Phase 1 is everyone working against the signatures above. If a signature must change, the tech lead
updates this file first.

---

## 7. Tests

### 7.1 Engineer (new)

* `test_muscles.py`: the AI_COACH §12 muscle cases; `EquipmentKey` values == `forms.EQUIPMENT` keys;
  `FOCUS_MUSCLES` keys ⊇ `forms.FOCUS` keys.
* `test_recovery.py`: every §3.4 fixture row; status thresholds; `rec_class` boundaries
  (84/85, 69/70, 49/50, 24/25, never trained → `rec-na`); legacy entries expanded by `sets_done`;
  tracked entries with 0 logs contribute nothing; neglect rules (new user → `[]`).
* `test_strength.py`: §5.1 example (overall 111); `None` cases; stale; PR; `delta_28d`; `index_pct`;
  `sparkline_points` (numbers only, None for < 2).
* `test_sets.py`: check, uncheck and rate (HTML and `X-Requested-With: fetch` JSON); targets copied from
  `EntryPlan` and **not** from the form; `set_index` 0 accepted, 20 rejected; invalid rating rejected;
  entry summary synced; finished session rejected; another user's session, entry or set → 404; entry
  delete and session delete cascade `SetLog` + `EntryPlan`; `log_all`.
* `test_today_progress.py`: `/` renders for a brand-new user with no key (fallback hero, recovery panel);
  `/plan/rebuild` and `/plan/start` create a `DailyPlan`, session, entries and EntryPlans; `/plan/generate` without a
  key → fallback, 302; hero `active`/`done` states; `/progress` with and without data; `?month=` invalid
  and future values ignored; `/history?day=` and the default "Last 14 days"; `/exercises/<id>` for another user → 404.
* `test_swap_routes.py`: GET stores fallback options; apply choice; out-of-range choice → 404; swap after a
  logged set → 404; another user's entry → 404.
* `test_account_deletion.py`: rows in all 6 new tables are removed.
* `test_templates_csp.py`: scans `app/templates/**/*.html` and fails on `<script` without `src=`,
  `<style`, `style=`, or `\son[a-z]+=`. Also checks that the CSP string is unchanged.

### 7.2 AI agent (new)

AI_COACH §12 for `progression`, `fallback_plan` (incl. `kind="recovery"`, determinism, bodyweight-only
empty library ≥ 3 exercises), `finalize_plan`, `generate_today` and `suggest_swaps` kwargs (`effort`
medium/low, `fallbacks`, `betas`, `json_schema`), sanitizing `</user_notes>`, the budget
(`AI_DAILY_CALLS=1`), and `generations` ≥ 5 → fallback. **Existing `test_ai.py` tests stay unchanged.**

### 7.3 Allowed edits to existing tests

Only `tests/test_youtube.py::test_exercise_with_video_embeds_nocookie` and
`::test_exercise_without_video_shows_search_link` change, so that they GET `/exercises/<id>` instead of
`/exercises` (C16). Every other existing test passes **unmodified**.

---

## 8. Spec deltas each owner applies to their own doc

* **DESIGN.md (designer):** remove hero state B; rest state = `plan.kind == "recovery"`; `.rec-*`
  thresholds from 2.3; rating value `ok` labelled "Good"; Strength Index is self-relative (100 = baseline) with
  no levels and the ring fill from `index_pct`; no `user_pref`, theme toggle or bodyweight row; swap is a
  page (`swap.html`) and only before any set is logged; set routes from 4.1; `history?day=`.
* **AI_COACH.md (AI agent):** `TodayPlan.kind`; `DailyPlan.swaps_json` removed (swaps live on `EntryPlan`);
  plan-level swap routes replaced by entry routes; `SetLogForm` has no `plan_index` and no `unit` (unit from
  settings); `/recovery` and `/strength` cut; `recovery_view` replaced by `recovery_panel` with `.rec-*`;
  `TrainerContext` lives in `plan_schema.py`; `generations` default 0, counting AI generations only.
* **README / ARCHITECTURE / SECURITY (engineer):** new tables and data flows; B8 adds the per-user
  limits and the `AIUsage` daily budget (`AI_DAILY_CALLS`); `.env.example` gets `AI_DAILY_CALLS=25`; gunicorn
  `--timeout 120`.

---

## 9. Acceptance checklist (integrator verifies)

Run from `workout_app/`:

- [ ] `pytest -q`: all tests green; the 71 originals pass, with only the two `test_youtube` edits from 7.3.
- [ ] `ruff check .` and `ruff format --check .` are clean. `bandit -q -c pyproject.toml -r app` reports nothing new.
- [ ] `grep -rnE "<style|style=|\son[a-z]+=" app/templates` returns nothing. Every `<script` has `src=`.
      `security.CSP` is unchanged.
- [ ] An existing `workout.db` from before this change starts fine (`create_all` adds tables only), and old
      sessions render as legacy entries.
- [ ] With **no `ANTHROPIC_API_KEY`**: a new user sees a full-body fallback plan on Today, can start it,
      check off sets (with and without JS), rate them, see the rest timer (JS), swap an exercise
      (fallback options), finish, see the summary (volume, ratings, next time, PRs on the second run),
      and see recovery and the Strength Index update.
- [ ] With a key (fake client in tests): `/plan/generate` calls Claude once with `json_schema`,
      `fallbacks="default"` and the beta header. `GET /` never calls Claude. The budget cap falls back.
- [ ] Ownership: another user's session, entry, set, exercise or plan → 404 on every new route.
- [ ] Existing features: password and Google login, manual exercises, AI builder preview/save, YouTube
      embed on detail plus a search link, Oura/Apple sync, per-session calories (refresh, Apple Shortcut,
      manual), share links (gym and no-gym day), settings, logout everywhere, account deletion (new tables
      emptied).
- [ ] UI: dark theme; 4-tab bar with `aria-current`; no emoji icons; Now bar on non-session pages
      while a session is open; focus mode hides the tab bar; all targets ≥ 44 px; inputs ≥ 16 px; no horizontal
      scroll at 320 px or at 200 % zoom; `prefers-reduced-motion` respected.
- [ ] Public share page: no tab bar, no recovery or strength data, and no username.
- [ ] No other app's name, logo, icon, artwork or copy appears anywhere in templates, static assets or docs.
