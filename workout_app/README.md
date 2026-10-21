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
| Share your day with a friend | Expiring, revocable link showing exercises + calories, or that you **didn't** go to the gym; uses the phone's native share sheet |

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

## Checks (the same ones CI runs)

```bash
ruff check . && ruff format --check .   # lint + style
bandit -c pyproject.toml -r app         # security static analysis
pip-audit -r requirements.txt           # known-vulnerable dependencies
pytest -q                               # 70+ tests incl. auth, IDOR, CSRF, API, sharing
```

## Deploy (Render example)

1. New **Web Service** → this repo → **Root Directory** `workout_app`.
2. Build: `pip install -r requirements.txt`
3. Start: `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2`
4. Add a **Render Postgres** database and set `DATABASE_URL` (SQLite data is lost on redeploy).
5. Set env vars from `.env.example` as secrets: at minimum `APP_ENV=production`,
   `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `DATABASE_URL`, `PROXY_FIX_HOPS=1`.
6. With more than one worker or instance, point `RATELIMIT_STORAGE_URI` at Redis.

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
│   ├── workouts/            # today, history, gym, exercises, AI builder, sessions
│   ├── integrations/        # Oura OAuth, Apple tokens, Apple Health API
│   ├── sharing/             # share links + public page
│   ├── services/            # ai_coach, oura, youtube, calories (no Flask routes)
│   ├── templates/           # Jinja templates (autoescaped)
│   └── static/              # css, js (no inline JS), manifest, icons
├── tests/                   # pytest suite
└── docs/
    ├── ARCHITECTURE.md      # how the pieces fit, data flow, decisions
    ├── SECURITY.md          # threat model, trust boundaries, controls, checklist
    └── INTEGRATIONS.md      # Google, Oura, Apple Shortcut, YouTube, Anthropic setup
```
