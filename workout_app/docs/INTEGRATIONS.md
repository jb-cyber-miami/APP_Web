# Integrations setup

Every integration is optional. Set the environment variables, restart, and the feature
appears in the app.

## Google sign-in

1. Go to <https://console.cloud.google.com/apis/credentials> → **Create credentials → OAuth client ID** → *Web application*.
2. Authorized redirect URI: `https://YOUR-DOMAIN/auth/google/callback`
   (local: `http://127.0.0.1:5000/auth/google/callback`).
3. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

A Google sign-in creates its own account. To use both methods on one account, sign in with
your password first, then go to **Me → Link Google account**.

## Oura Ring

Oura retired personal access tokens in December 2025, so OAuth2 is the only option.

1. Create an application at <https://cloud.ouraring.com/oauth/applications>.
2. Redirect URI: `https://YOUR-DOMAIN/integrations/oura/callback`.
3. Scopes used: `daily` (daily active/total calories) and `workout` (workout calories + times).
4. Set `OURA_CLIENT_ID`, `OURA_CLIENT_SECRET`, and a `TOKEN_ENCRYPTION_KEY`:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
5. In the app: **Sync → Connect Oura**.

Data used: `GET /v2/usercollection/daily_activity` (`active_calories`, `total_calories`) and
`GET /v2/usercollection/workout` (`calories`, `start_datetime`, `end_datetime`, `activity`).
Oura syncs automatically when you finish a workout, and you can also press **Sync now**.
Unapproved Oura apps are limited to about 10 users, which is plenty for personal use.

## Apple Watch / Apple Health (iPhone Shortcuts)

Apple has no web API for Health data. Your iPhone pushes the numbers instead.

### 1. Create a token
In the app: **Sync → Create token**. Copy the `wk_…` value (it's shown once).

### 2. Shortcut A: "Daily Calories"
In the **Shortcuts** app → **+**:

1. **Find Health Samples** where *Type* is *Active Energy* and *Start Date* *is today*.
2. **Calculate Statistics**: *Sum* of *Health Samples*.
3. **Format Date**: *Current Date*, format *Custom* `yyyy-MM-dd`.
4. **Get Contents of URL**: `https://YOUR-DOMAIN/api/v1/apple/daily`
   * Method **POST**
   * Headers: `Authorization` = `Bearer wk_…your token…`
   * Request Body **JSON**: `day` (Text) = *Formatted Date*; `active_kcal` (Number) = *Statistics*

Automate it: **Automation → + → Time of Day → 9:00 PM, Daily → Run Immediately** → run *Daily Calories*.

### 3. Shortcut B: "Workout Calories" (exact name matters)
The **⌚ Send Apple Watch calories** button on a finished workout opens this Shortcut
with the text `SESSION_ID|START|END` (UTC times).

1. **Receive Text** input from *Shortcuts* (if there's no input: *Ask For* text).
2. **Split Text** *Shortcut Input* by *Custom* `|`.
3. **Get Item from List**: *Item At Index 1* → set variable `SessionID`.
   Same for index 2 → `Start` and index 3 → `End`.
4. **Get Dates from Input** for `Start` and for `End`.
5. **Find Health Samples** where *Type* is *Active Energy*, *Start Date is after* `Start`,
   *End Date is before* `End`.
6. **Calculate Statistics**: *Sum*.
7. **Get Contents of URL**: `https://YOUR-DOMAIN/api/v1/apple/workout`, **POST**, same
   `Authorization` header, JSON body:
   `start` (Text) = `Start`, `end` (Text) = `End`, `active_kcal` (Number) = *Statistics*,
   `session_id` (Number) = `SessionID`, `activity` (Text) = `Strength`.
8. **Open URLs**: `https://YOUR-DOMAIN/sessions/` + `SessionID` to jump back to the app.

### API reference

```
POST /api/v1/apple/daily
Authorization: Bearer wk_...
{"day": "2026-10-21", "active_kcal": 642, "total_kcal": 2480}      # total_kcal optional

POST /api/v1/apple/workout
Authorization: Bearer wk_...
{"start": "2026-10-21T22:05:00Z", "end": "2026-10-21T23:01:00Z",
 "active_kcal": 386, "activity": "Strength", "session_id": 12}       # session_id optional
```

Responses: `200` ok, `401` bad or missing token, `422` validation error (details listed), `429` rate limited.
Times must include a timezone (`Z` or `-04:00`). A workout window can be at most 6 hours long.
Sending the same data again updates the existing record, so retries are safe.

## AI workout builder (Anthropic Claude)

1. Create a key at <https://console.anthropic.com/> and **set a monthly spend limit**.
2. Set `ANTHROPIC_API_KEY`. Optional: `AI_MODEL` (default `claude-opus-5-5`).
3. In the app: **Exercises → My gym** (equipment, goal, limitations), then **✨ Build with AI**.

Each build is a single API call with structured JSON output. If the model declines, the
request is retried server-side on Anthropic's recommended fallback model
(`fallbacks: "default"`). Users get 10 builds per hour.

## YouTube videos

* Any exercise can hold a YouTube link (watch, youtu.be, shorts, embed URLs all work).
* With `YOUTUBE_API_KEY` set (YouTube Data API v3, Google Cloud Console), new manual and AI
  exercises get a tutorial video automatically.
* Without a key, exercises show a "Find a how-to video on YouTube" search link instead.
