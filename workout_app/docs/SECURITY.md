# Security model

This app holds login credentials, health data (calories, workouts) and third-party OAuth
tokens. It is designed so that a mistake in one place doesn't expose everything.

## Trust boundaries

```
 ┌──────────────── untrusted ────────────────┐      ┌──────────── trusted ─────────────┐
 │ Browser (any user, any input)             │      │ Flask app process                │
 │ iPhone Shortcut (holder of an API token)  │ ───► │  ├ validation (WTForms/pydantic) │
 │ Friend with a share link                  │      │  ├ ownership checks              │
 │ Responses from Oura / Google / YouTube    │      │  └ DB via SQLAlchemy ORM         │
 │ Output of the AI model                    │      │ Environment secrets              │
 └───────────────────────────────────────────┘      └──────────────────────────────────┘
```

Everything on the left is treated as hostile input, **including AI output and partner APIs**.

| # | Boundary | Who crosses it | Controls |
|---|---|---|---|
| B1 | Browser → app | Signed-in user | Session cookie (`Secure`, `HttpOnly`, `SameSite=Lax`), CSRF token on every POST, server-side form validation, rate limits |
| B2 | App → database | App only | ORM (parameterised SQL), `owned_or_404()` on every user-owned row, 404 (not 403) for others' data |
| B3 | Anonymous → `/login`, `/register` | Anyone | Rate limit (10/min, 50/h per IP), generic error message, constant-time-ish check against a dummy hash, scrypt hashes |
| B4 | Google → `/auth/google/callback` | Google | Authlib verifies `state`, `nonce` and the ID token signature; requires `email_verified`; **no automatic merging** with password accounts by email (account takeover risk) |
| B5 | Oura ↔ app | Oura API | OAuth2 with `state`; tokens Fernet-encrypted at rest; refresh tokens rotated; revoked on disconnect/account delete; responses parsed defensively and clamped |
| B6 | iPhone Shortcut → `/api/v1/apple/*` | Holder of a token | 256-bit random bearer token, stored as SHA-256 only, shown once, revocable, max 5 per user; pydantic schema with ranges; can only write to its owner's data; rate limited per token; CSRF-exempt because it uses no cookies |
| B7 | Friend → `/s/<token>` | Anyone with the link | 256-bit random token, SHA-256 at rest, expires (7 days by default), revocable, `noindex`, `Referrer-Policy: no-referrer`; shows only display name, day, exercises, calories, never username, email or other days. "Trained today" needs a logged set (or a legacy entry / finished session with calories); a merely started plan reads as "didn't go", and only logged exercises are listed |
| B8 | App → Anthropic | App only | Key server-side only; user free text sanitized (`<`/`>` stripped), length-limited and wrapped in tags; structured JSON output with enums; re-validated with pydantic, clamped, equipment-filtered; history overrides AI weights; plans and swap options stored only after validation and re-validated on load; forms post only bounds-checked integers (`choice`, `set_index`); rendered autoescaped; **no AI call on GET**; per-user rate limits (`/plan/generate` 6/h and 20/day, swaps 30/h, AI builder 10/h) plus a **database daily budget** (`AIUsage`, `AI_DAILY_CALLS`, default 25) shared by every Claude call; logs never contain prompt or response text |
| B9 | YouTube embeds in browser | YouTube | Only an 11-char ID matching `^[A-Za-z0-9_-]{11}$` is stored; host allow-list when parsing; `youtube-nocookie.com` iframe with `sandbox`; CSP `frame-src` restricted to that host |

## Controls in code

| Threat (OWASP Top 10) | Where it's handled |
|---|---|
| A01 Broken access control / IDOR | `security.owned_or_404`, `@login_required`; child rows are also checked (`entry.session_id == ws.id`, `set.entry.session_id == ws.id`) and 404 on mismatch; `exercise_id` values from stored plan JSON are re-checked against the owner; tests in `tests/test_security.py`, `test_sets.py`, `test_swap_routes.py`, `test_today_progress.py` |
| A02 Cryptographic failures | scrypt passwords (Werkzeug), Fernet for OAuth tokens, SHA-256 for random tokens, HSTS, `Secure` cookies |
| A03 Injection / XSS | SQLAlchemy ORM; Jinja autoescape (AI text included, never `|safe`); **no inline JS, no `<style>`, no `style=""`, no `on*=`** so CSP is `script-src 'self'; style-src 'self'` (enforced by `tests/test_templates_csp.py`); dynamic sizes only through clamped SVG attributes; JS uses `textContent`, never `innerHTML` with server data (the broken-thumbnail fallback builds its SVG with `createElementNS` from a constant path) |
| A04 Insecure design | Least data on share pages; manual review step before AI output is saved |
| A05 Security misconfiguration | `create_app` refuses to start in production without a strong `SECRET_KEY`, or without `TOKEN_ENCRYPTION_KEY` when Oura is on; no debug in prod; custom error pages (no stack traces) |
| A06 Vulnerable components | Pinned `requirements.txt`, `pip-audit` + Dependabot in CI |
| A07 Auth failures | Rate limits, session rotation on login (`session.clear()`), `session_version` so password change / "sign out everywhere" kills old cookies, password policy (≥10 chars, ≠ username) |
| A08 Integrity | OAuth `state`/`nonce`; CSRF on all forms; logout is POST |
| A09 Logging | Failed logins and integration errors are logged **without** passwords or tokens |
| A10 SSRF | The server only calls fixed, hard-coded hosts; no user-supplied URLs are fetched |

HTTP headers on every response: `Content-Security-Policy`, `Strict-Transport-Security`,
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` (+ `frame-ancestors 'none'`),
`Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, and
`Cache-Control: no-store` on signed-in pages.

## Secrets

* All secrets come from environment variables (`.env` locally, the host's secret store in prod).
* `.env`, `*.db` are git-ignored. **Never commit real keys.** If one leaks, rotate it at the provider.
* Rotating `SECRET_KEY` signs everyone out. Rotating `TOKEN_ENCRYPTION_KEY` makes stored Oura
  tokens unreadable, so users must reconnect Oura.

## Privacy

* Users can delete their account (Settings), which deletes all rows and revokes the Oura grant.
* Health data is only used to show the user their own numbers and what they choose to share.
* Sending gym notes to Anthropic happens only when the user presses "Build my workout",
  "Build with AI coach" or asks for AI swap options. The prompt holds no e-mail, username or
  display name, only training data, equipment and the user's notes.
* Account deletion removes every coach table (`ai_usage`, `daily_plan`, `set_log`, `entry_plan`,
  `exercise_muscles`, `trainer_settings`) before the existing rows.
* Public share pages show no recovery, strength or plan data.

## Pre-release checklist

- [ ] `APP_ENV=production`, 32+ char `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY` set
- [ ] Postgres `DATABASE_URL`; backups enabled
- [ ] HTTPS only (Render does this); `PROXY_FIX_HOPS` matches the number of proxies
- [ ] Redis `RATELIMIT_STORAGE_URI` if more than one worker/instance
- [ ] OAuth redirect URIs registered exactly (`https://…/auth/google/callback`, `https://…/integrations/oura/callback`)
- [ ] Anthropic key has a monthly spend limit set in the Console; `AI_DAILY_CALLS` set (default 25)
- [ ] gunicorn `--timeout 120` (AI plan generation can take tens of seconds)
- [ ] CI green: ruff, bandit, pip-audit, pytest
- [ ] Old quiz `users.json` / default credentials not reused here

## Reporting a vulnerability

Please email the maintainer privately instead of opening a public issue.
