# FitLog: workout tracker for your phone

A mobile-first web app for running your gym routine from your phone.

| Feature | How |
|---|---|
| Fits any phone screen, installable on the home screen | Mobile-first CSS, safe-area insets, Web App Manifest (Safari → Share → *Add to Home Screen*) |
| Add exercises manually | Exercise library with sets, reps, rest, how-to notes |
| AI builds exercises for **your** gym | Gym profile (size + equipment + goal + limitations) → Claude generates a workout you review before saving |
| YouTube how-to videos | Paste a link, or let the app find one (YouTube Data API); embedded privacy-friendly via `youtube-nocookie.com` |
| Sign in with username/password **or** Google | scrypt-hashed passwords; Google OpenID Connect |
| Oura Ring sync | OAuth2 → daily active calories + workouts |
| Apple Watch / Apple Health sync | iPhone Shortcut posts calories to a token-protected API |
| Calories at the end of each session | Wearable workout calories that overlap the session window are attributed to it (Apple preferred, Oura fallback, manual override) |
| Share your day with a friend | Expiring, revocable link showing the exercises you actually logged + calories, or that you **didn't** go to the gym; uses the phone's native share sheet. Starting a plan without logging a set doesn't count as a gym day |
| AI coach: **Today's workout** | One tap plans today's session around muscle recovery, recent training and neglected muscles. Claude composes it when a key is set; a deterministic plan is always available without one |
| Muscle **Recovery** | 0–100 % per muscle group from the last 7 days of logged sets (dose and time based), shown as a body map with a Fatigued → Fresh colour scale |
| Set-by-set logging | Check off each set, rate it Easy / Good / Hard, rest timer, add a set, swap an exercise before you start it. Only the current exercise is expanded; finishing one opens the next. Each exercise keeps the unit (lb/kg) it was planned in, so switching units mid-workout never mislabels a set |
| Progressive overload: **Next targets** | Estimated 1RM (Epley) from your history prescribes next session's weight and reps per set; ratings steer the next prescription |
| **Strength Index** | Your lifts compared with your own starting point (100 = baseline), per muscle group and overall, plus PRs, streak and a monthly calendar |

## Quick start (local)

```bash
cd workout_app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # then fill in SECRET_KEY and TOKEN_ENCRYPTION_KEY
flask --app wsgi run --debug    # http://127.0.0.1:5000
```

Open it on your phone on the same Wi‑Fi with `flask --app wsgi run --host 0.0.0.0`
and browse to `http://<your-computer-ip>:5000`.

All integrations are optional. The app runs with only `SECRET_KEY` set, and each
feature switches on when its keys are present. See [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

### Database

Tables are created with `db.create_all()` at start-up. This release only **adds** tables
(`trainer_settings`, `exercise_muscles`, `entry_plan`, `set_log`, `daily_plan`, `ai_usage`),
so an existing `workout.db` keeps working and old sessions show up as plain (legacy) entries.
No need to delete it. (The later review fixes changed behaviour only, not the schema: no new
tables or columns.)

## Checks (the same ones CI runs)

```bash
ruff check . && ruff format --check .   # lint + style
bandit -c pyproject.toml -r app         # security static analysis
pip-audit -r requirements.txt           # known-vulnerable dependencies
pytest -q                               # 350+ tests incl. auth, IDOR, CSRF, API, sharing, coach
```

## Deploy (Render)

The repository root has a `render.yaml` Blueprint that creates everything in one go: the web
service (root `workout_app`), a private Postgres database, and auto-generated `SECRET_KEY`
and `TOKEN_ENCRYPTION_KEY`.

1. Merge this code into the branch Render deploys (normally `main`).
2. Render dashboard → **New → Blueprint** → choose this repository → **Apply**.
3. When asked for the optional keys (`ANTHROPIC_API_KEY`, Google, Oura, YouTube), paste the
   ones you have and leave the rest empty. You can add them later under **Environment**.
4. Open the `https://fitlog-xxxx.onrender.com` address Render shows, create your account, and
   on the iPhone: Safari → Share → **Add to Home Screen**.
5. Register the redirect URIs with Google/Oura using that address (see docs/INTEGRATIONS.md).

Cost: about $13/month (Starter web $7 + Basic Postgres $6). Free plans work for a trial, but
the free web service sleeps after 15 idle minutes and a free database is deleted after 30 days.
Render backs up paid Postgres databases automatically.

Manual setup without the Blueprint: build `pip install -r requirements.txt`, start
`gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`, and set
`APP_ENV=production`, `PROXY_FIX_HOPS=1`, `DATABASE_URL`, `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`
(any random string of 32+ characters). With more than one worker or instance, point
`RATELIMIT_STORAGE_URI` at Redis. `AI_DAILY_CALLS` (default 25) caps Claude calls per user per day.

## Project layout

```
workout_app/
├── wsgi.py                  # entry point (gunicorn wsgi:app)
├── app/
│   ├── __init__.py          # app factory, config checks, error handlers
│   ├── config.py            # all settings from environment variables
│   ├── extensions.py        # db, CSRF, login, rate limiter, OAuth
│   ├── models.py            # SQLAlchemy models
│   ├── security.py          # ownership checks, tokens, encryption, headers/CSP
│   ├── forms.py             # server-side validation for every form
│   ├── timeutil.py          # UTC storage ↔ user timezone
│   ├── auth/                # register, login, Google, settings, delete account
│   ├── workouts/            # today, history, progress, gym, exercises, AI builder, plan, sessions, sets, swaps
│   ├── integrations/        # Oura OAuth, Apple tokens, Apple Health API
│   ├── sharing/             # share links + public page
│   ├── services/            # no Flask routes:
│   │                        #   ai_coach, oura, youtube, calories (integrations)
│   │                        #   muscles, recovery, progression, strength (coach maths, deterministic)
│   │                        #   plan_schema, catalog, trainer (Today's plan, swaps)
│   │                        #   sets, summary, activity, prefs (logging and view models)
│   ├── templates/           # Jinja templates (autoescaped)
│   └── static/              # css, js (no inline JS), manifest, icons
├── tests/                   # pytest suite
└── docs/
    ├── ARCHITECTURE.md      # how the pieces fit, data flow, decisions
    ├── SECURITY.md          # threat model, trust boundaries, controls, checklist
    ├── BUILD_CONTRACT.md    # AI coach build contract (data model, APIs, routes)
    ├── AI_COACH.md          # coach logic: recovery, progression, strength, AI prompts
    ├── DESIGN.md            # visual design system and screens
    └── INTEGRATIONS.md      # Google, Oura, Apple Shortcut, YouTube, Anthropic setup
```
