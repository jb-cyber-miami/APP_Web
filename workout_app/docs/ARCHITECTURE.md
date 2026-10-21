# Architecture

## Shape of the app

A server-rendered Flask application. A server-rendered app was chosen over a JavaScript
SPA and separate API on purpose:

* One deployable unit, one language (Python), and no build step. It's easy to run on Render.
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

**Daily totals** come from *daily* samples (Oura `daily_activity`, Apple daily shortcut).
They are shown per source, and the headline number prefers Apple.

**Did I go to the gym?** A day counts as "went" if any session on that day was finished or
has logged exercises. Otherwise history and share pages show "No gym".

## Time

All timestamps are stored as naive UTC. A session's `day` is computed in the user's timezone
(Settings → Timezone, default `America/New_York`), so a 11 PM workout belongs to that day.

## Database

`db.create_all()` runs at start-up, which is fine for a first version. Before changing models
in production, add **Flask-Migrate (Alembic)** so schema changes are versioned and reversible.

## Adding a feature: checklist

1. Put logic in `services/`, keep the route thin.
2. New table owned by a user? Add `user_id` and load rows only via `owned_or_404`.
3. New form? A `FlaskForm` class in `forms.py` with length/range validators.
4. New state-changing route? `methods=["POST"]` (CSRF is automatic), never GET.
5. New external host? Add it to the CSP in `security.py` only if the *browser* must reach it.
6. New secret? Environment variable + `.env.example` entry, never in code.
7. Tests: the happy path, another user's data (expect 404), and invalid input.
