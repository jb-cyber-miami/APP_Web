# Architecture

## Shape of the app

A server-rendered Flask application. A server-rendered app was chosen over a JavaScript
SPA and separate API on purpose:

* One deployable unit, one language (Python), and no build step. It runs as one container on Cloud Run behind Firebase Hosting.
* Secrets (Anthropic, Oura, Google, YouTube keys) never leave the server.
* HTML forms + CSRF tokens + `SameSite` cookies give strong defaults with little code.
* It still feels like an app on a phone: mobile-first CSS, bottom tab bar, a
  Web App Manifest for "Add to Home Screen", and the native share sheet via `navigator.share`.

```
 Phone browser ──HTTPS──► Flask (gunicorn) ──► Postgres / SQLite
                              │
                              ├──► Anthropic API (AI workout builder)
                              ├──► Oura API v2 (OAuth2, pull)
                              ├──► Google OIDC (sign-in)
                              └──► YouTube Data API (optional video lookup)

 iPhone Shortcut ──HTTPS + Bearer token──► /api/v1/apple/* (push)
 Friend's browser ──HTTPS──► /s/<random token> (read-only, expiring)
```

## Layers

| Layer | Folder | Rule |
|---|---|---|
| Routes (blueprints) | `auth/`, `workouts/`, `integrations/`, `sharing/` | Thin: parse the request, check ownership, call a service, render. |
| Services | `services/` | Business logic and third-party calls. No `request`, no templates. Unit-testable. |
| Models | `models.py` | Data only. Every user-owned table has `user_id`. |
| Cross-cutting | `security.py`, `forms.py`, `timeutil.py` | Shared controls; never re-implemented per route. |

## Key data flows

**AI workout builder**
`GymProfile` + form choices → `services/ai_coach.build_prompt` → Claude
(`claude-opus-5-5`, structured JSON output, server-side refusal fallback) →
pydantic validation → **preview page** → the user ticks exercises → `/exercises/ai/save`
re-validates every field (hidden inputs are user-controlled) → `Exercise` rows (+ optional
YouTube lookup).

**Calories for a session**
1. Start a session → `started_at` (UTC). Log exercises. Finish → `ended_at`.
2. On finish: if Oura is connected, sync the last 2 days.
3. `services/calories.session_calories` collects wearable *workout* samples that overlap
   the session window (±10 min), pro-rates partial overlaps, and picks a source.
   Apple wins over Oura, and the two are **never summed** because both measure the same body.
4. Apple data can arrive later: the session page has a button that runs the iPhone
   Shortcut with the session window, which posts to `/api/v1/apple/workout` with
   `session_id` and updates the session immediately.
5. A manual number always wins and is never overwritten by later syncs.

**Today's workout (AI coach)**
1. `GET /` → `trainer.today_plan`: today's stored `DailyPlan` if valid, else an **unsaved**
   deterministic `fallback_plan`. GET never calls Claude and never writes.
2. `POST /plan/generate` → `trainer.build_context` (gym, recovery, neglected muscles, last 5
   sessions, library) → `ai_coach.generate_today` (structured JSON, `effort="medium"`) →
   pydantic re-validation → `trainer.finalize_plan` (equipment filter, library resolution,
   recovery guard, **history wins** for weights/reps) → `DailyPlan.plan_json`. No key, an
   `AIError`, the daily budget (`AIUsage`) or 5 generations today → the fallback plan with a notice.
   `POST /plan/rebuild` is the same without AI.
3. `POST /plan/start` → `trainer.start_plan`: a `WorkoutSession` with one `SessionEntry` +
   `EntryPlan` (per-set targets, converted to the user's unit) per planned exercise; unknown
   exercises are added to the library. Starting alone is **not** training (see below).

**Set-by-set logging**
* An entry with an `EntryPlan` is *tracked*. A `SetLog` row exists only for a **completed** set;
  open rows are rendered from the targets. Targets on a `SetLog` are copied server-side from the
  `EntryPlan`, never from the form.
* **One unit per tracked entry.** `EntryPlan.unit` is the unit of its targets, its prefilled weights,
  the set-row label and every `SetLog` written for it. `start_plan` / `apply_swap` / `attach_targets`
  convert planned weights to the user's current unit (rounded to plates) when the entry is created,
  so a plan built in lb and started in kg is stored in kg. Changing the unit mid-workout does not
  touch open entries; history converts each `SetLog` by its own `unit`.
* `POST /sessions/<sid>/entries/<eid>/sets` (check), `/sessions/<sid>/sets/<id>/delete` (uncheck),
  `/rate` (Easy/Good/Hard). With `X-Requested-With: fetch` they return JSON for `workout.js`;
  without JS they redirect back to `#ex-<eid>`.
* `sets.sync_entry` keeps the legacy `sets_done/reps_done/weight` fields in step, so history,
  share pages and older code keep working. Entries without an `EntryPlan` are *legacy* and are
  expanded by `sets_done` wherever sets are counted.
* Swap: only on a tracked entry with 0 logged sets in an open session. Options (AI or
  fallback) are stored on `EntryPlan.swaps_json`; the form posts only an integer `choice`.

**Coach maths (deterministic, no AI, `now` passed in)**
`muscles` (taxonomy) → `recovery` (7-day dose model) / `progression` (Epley, next targets) →
`strength` (self-relative Strength Index) / `summary` (volume, PRs, next time) /
`activity` (week strip, calendar, streak). Import graph has no cycles; `ai_coach` never imports
`trainer`.

**Daily totals** come from *daily* samples (Oura `daily_activity`, Apple daily shortcut).
They are shown per source, and the headline number prefers Apple.

**Did I go to the gym?** One rule, `calories.session_trained` (Python) and its SQL twin in
`activity._day_stats` (week strip, calendar, streak). A session counts when it has a *legacy*
entry (no `EntryPlan`) or a tracked entry with at least one `SetLog`, or when it was finished and
is either empty (a quick "I went") or has calories. A just-started plan, whose entries are all
untouched, is not a gym day, even after Finish. Otherwise history and share pages show "No gym".
Share pages and history list only exercises that recorded training (`DaySummary.logged_entries`).

## Time

All timestamps are stored as naive UTC. A session's `day` is computed in the user's timezone
(Settings → Timezone, default `America/New_York`), so a 11 PM workout belongs to that day.

## Database

`db.create_all()` runs at start-up, which is fine for a first version. Before changing models
in production, add **Flask-Migrate (Alembic)** so schema changes are versioned and reversible.

The AI coach release only adds tables, so `create_all` upgrades an existing database in place:

| Table | Holds | Notes |
|---|---|---|
| `trainer_settings` | unit (lb/kg), default minutes | lazily created; absence = defaults |
| `exercise_muscles` | canonical primary/secondary muscles + pattern per library exercise | `rules`/`ai`/`user` source; a `user` row is never overwritten |
| `entry_plan` | per-entry set targets, rest, progression note, swap options | makes an entry *tracked* |
| `set_log` | one completed set (reps/seconds, weight, unit, rating, copied targets) | unique per entry + set index |
| `daily_plan` | today's validated plan JSON, AI generation count, linked session | unique per user + day |
| `ai_usage` | Claude calls and tokens per user per UTC day | the real cost cap |

Every new table carries `user_id`. SQLite doesn't enforce `ondelete`, so deletes go through the
ORM cascades, `exercise_delete` nulls `SessionEntry.exercise_id`, `session_delete` nulls
`DailyPlan.session_id`, and account deletion removes the new tables first.

## Adding a feature: checklist

1. Put logic in `services/`, keep the route thin.
2. New table owned by a user? Add `user_id` and load rows only via `owned_or_404`.
3. New form? A `FlaskForm` class in `forms.py` with length/range validators.
4. New state-changing route? `methods=["POST"]` (CSRF is automatic), never GET.
5. New external host? Add it to the CSP in `security.py` only if the *browser* must reach it.
6. New secret? Environment variable + `.env.example` entry, never in code.
7. Tests: the happy path, another user's data (expect 404), and invalid input.
