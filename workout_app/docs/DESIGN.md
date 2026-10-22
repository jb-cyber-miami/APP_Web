# FitLog design spec: "Athletic Dark"

Status: v1.1 (2026-10-21), aligned with `docs/BUILD_CONTRACT.md` (which wins on any conflict). Owner: product design. Implemented in `app/templates/**` and `app/static/**`; routes by the software engineer, daily plan / prescriptions / recovery by the AI agent.

FitLog is moving to the feel of a modern AI strength coach on iPhone: dark, high-contrast,
bold numbers, one obvious next action. Other AI-trainer apps are a reference for **feel and
feature set only**. We do not copy any product's name, logo, icons, illustrations, wording or
screenshots. Every icon, colour, label and figure below is original to FitLog.

Design principles

1. **One next action per screen.** Today has one Start button, an active workout has the current set, and a summary has Share.
2. **Numbers are the hero.** Weight, reps, kcal, % recovered and score use the heaviest weight and tabular figures.
3. **Colour has a job.** The accent means "you can tap this / do this". Green, amber and red mean recovery or status. They are never mixed.
4. **Thumb-first.** Primary actions sit in the bottom 40% of the screen. Every target is at least 44 × 44 px.
5. **CSP-clean by construction.** No inline script, `<style>` or `style=""`. Dynamic values reach the page as SVG attributes or classes (see section 8).

---

## 1. Visual identity

### 1.1 Theme strategy

* **Dark is the default.** `<html data-theme="dark">` is rendered server-side.
* **Light variant.** The light tokens exist in CSS under `[data-theme="light"]`, but iteration 1 always renders dark. A theme toggle (and storing the choice) is **phase 2** (contract 0.2).
* `<meta name="theme-color" content="#0A0A0B">`, and `manifest.webmanifest` `background_color` / `theme_color` are set to `#0A0A0B`.
* `color-scheme: dark` on `[data-theme=dark]` and `light` on `[data-theme=light]`, so native controls (date pickers, scrollbars) match.

### 1.2 Colour tokens

All colours are CSS custom properties on `:root[data-theme=…]` in `app/static/css/app.css`. Components use only tokens, never raw hex.

| Token | Dark (default) | Light | Use |
|---|---|---|---|
| `--bg` | `#0A0A0B` | `#F4F4F1` | Page background |
| `--surface-1` | `#141416` | `#FFFFFF` | Cards |
| `--surface-2` | `#1C1C1F` | `#F0F0EC` | Inputs, stat tiles, nested blocks |
| `--surface-3` | `#26262A` | `#E4E4DF` | Pressed states, tracks, body silhouette |
| `--line` | `#2C2C31` | `#DCDCD6` | Hairlines, card borders |
| `--text` | `#F5F5F7` | `#0B0B0C` | Primary text |
| `--text-2` | `#A1A1AA` | `#52525B` | Secondary text, labels (AA on all surfaces) |
| `--text-3` | `#8B8B94` | `#6B6B73` | Tertiary text, 13px+ only (AA 4.5:1 on `--bg`/`--surface-1`) |
| `--accent` | `#D4FF3A` | `#D4FF3A` | **Brand "volt".** Primary button fill, active ring, today marker |
| `--on-accent` | `#0A0A0B` | `#0A0A0B` | Text/icons on accent fills (17:1) |
| `--accent-ink` | `#D4FF3A` | `#3F6212` | Accent used **as text or icon** (active tab, links). Light needs a dark ink for AA |
| `--accent-soft` | `rgba(212,255,58,.14)` | `rgba(132,204,22,.16)` | Selected chip background, hero glow |
| `--accent-line` | `#D4FF3A` | `#4D7C0F` | Accent as a **line, stroke or small mark** on a surface: current card/set border, check ring, ring/bar/spark fills, today rings, "muscles worked". Light needs ≥ 3:1 (WCAG 1.4.11) |
| `--ai` | `#A78BFA` | `#6D28D9` | AI badge, AI builder button |
| `--ai-soft` | `rgba(167,139,250,.16)` | `rgba(109,40,217,.10)` | AI chip background |
| `--ok` | `#3DDC84` | `#15803D` | Connected, PR, "went to gym" |
| `--warn` | `#FFC23D` | `#B45309` | Partial, needs attention |
| `--danger` | `#FF5C6C` | `#DC2626` | Destructive, errors, "no gym" |
| `--overlay` | `rgba(0,0,0,.6)` | `rgba(15,15,15,.35)` | Sheet/dialog backdrop |
| `--tabbar-bg` | `rgba(10,10,11,.82)` | `rgba(255,255,255,.86)` | Translucent tab bar (with `backdrop-filter: blur(20px) saturate(160%)`) |

The recovery scale tokens (`--rec-0` … `--rec-4`, `--rec-na`) are defined in section 5.

Contrast checks (dark): `--text` on `--surface-1` is 17.9:1, `--text-2` on `--surface-1` is 7.4:1, `--text-3` on `--bg` is 5.8:1, and `--accent` on `--bg` is 17:1. In light, `--accent-ink` `#3F6212` on white is 7.1:1. **The volt accent is never used for text on a light surface.**

### 1.3 Typography

System fonts only, with no external font host and no CSP change.

```css
--font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
--font-display: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
```

* All numbers use `font-variant-numeric: tabular-nums` (`.num` utility). Timers and set inputs must not jitter.
* Display styles use **weight 800–900, tight tracking**. Eyebrows are **uppercase, 600, wide tracking**. This combination produces the "athletic" look without a custom font.

| Token / class | Size / line-height | Weight | Tracking | Use |
|---|---|---|---|---|
| `.t-hero` | 44 / 44 | 900 | -0.03em | Big numbers: strength index, workout kcal, timer |
| `.t-display` | 34 / 38 | 900 | -0.02em | Large page title ("Today"), hero card title |
| `.t-title` | 24 / 28 | 800 | -0.01em | Section titles, exercise name in the active workout |
| `.t-headline` | 18 / 24 | 700 | 0 | Card titles, list row titles |
| `.t-body` | 16 / 24 | 400 | 0 | Body copy, **all inputs** |
| `.t-callout` | 15 / 20 | 500 | 0 | Meta lines ("3 × 10 · Chest") |
| `.t-footnote` | 13 / 18 | 500 | 0 | Hints, timestamps |
| `.eyebrow` | 12 / 16 | 700 | 0.08em, uppercase | Labels above values ("TODAY'S WORKOUT", "KCAL") |

The minimum rendered text is 12px, and only for uppercase eyebrows. Respect Dynamic Type by using `rem` for sizes with `html { font-size: 100% }`, and never set fixed heights on text containers.

### 1.4 Spacing, radii, elevation, motion

```css
/* 4-pt spacing */
--sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px; --sp-5: 20px; --sp-6: 24px; --sp-8: 32px; --sp-10: 40px;
--gutter: 16px;            /* page side padding; 20px at >= 390px wide */
/* radii */
--r-sm: 8px;   /* chips inside cards, set-row inputs */
--r-md: 12px;  /* buttons, inputs, stat tiles */
--r-lg: 18px;  /* cards */
--r-xl: 26px;  /* hero card, bottom sheets (top corners) */
--r-pill: 999px;
/* elevation: dark mode uses surface steps, not shadows */
--shadow-1: 0 1px 0 var(--line);                       /* card edge */
--shadow-2: 0 12px 32px rgba(0,0,0,.45);               /* sheets, rest timer, toast */
/* motion */
--ease: cubic-bezier(.2,.8,.2,1);
--dur-1: 120ms; --dur-2: 200ms; --dur-3: 320ms;
/* layout */
--tabbar-h: 64px; --nowbar-h: 56px; --timer-h: 76px;
```

* Cards have a `--surface-1` background, a `1px solid var(--line)` border and `--r-lg`. There are no drop shadows in dark mode. In light mode, cards get `--shadow-1` plus `0 2px 8px rgba(0,0,0,.04)`.
* The **hero card** uses `--r-xl` on `--surface-1` with a decorative volt glow from the stylesheet: `background-image: radial-gradient(120% 80% at 100% 0%, var(--accent-soft), transparent 60%)`.
* Max content width is 560px, centred. On wider screens the tab bar docks to that column (current behaviour).

### 1.5 Iconography

* **Inline SVG only**, through a Jinja macro `icon(name, cls="")` in `app/templates/_icons.html`. It outputs `<svg class="icon {{cls}}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">…</svg>`.
* Style: 24px grid, **2px stroke, round caps and joins, `fill="none"`, `stroke="currentColor"`**, drawn originally for FitLog. Colour is inherited from text colour classes, so no `style=""` is needed.
* Sizes: `.icon` 24px, `.icon-sm` 18px, `.icon-lg` 28px, `.icon-xl` 40px (empty states).
* **No emoji as primary icons.** Replace every emoji currently in the templates (🏠🏋️📅⌚⚙️✨📤▶🏁↻💍💪😴✅❌＋) with icons. Emoji may appear only inside user-typed text.
* Third-party brand marks: keep `static/icons/google.svg` on the Google button, because Google's sign-in guidelines require it. Do **not** draw Apple or Oura logos. Use generic `watch` and `ring` glyphs labelled with the product name in text.

Required icon set (names are macro keys):

| Name | Glyph idea | Used in |
|---|---|---|
| `today` | bolt inside a rounded square | Tab: Today |
| `library` | dumbbell, horizontal | Tab: Library |
| `progress` | three rising bars | Tab: Progress |
| `profile` | head and shoulders | Tab: Profile |
| `play` | triangle | Start / Continue |
| `check` | tick | Set done, connected |
| `plus` / `minus` | + / − | Add set, steppers |
| `swap` | two opposed arrows | Swap exercise |
| `video` | rounded rect + triangle | Watch how-to |
| `timer` | stopwatch | Rest timer |
| `share` | box with up arrow | Share |
| `sparkle` | 4-point star | AI |
| `flame` | flame | Calories |
| `trophy` | cup | PRs |
| `watch` | watch face with straps | Apple Watch / Health |
| `ring` | torus seen at 3/4 | Oura |
| `sync` | circular arrows | Refresh / sync |
| `calendar` | calendar | History |
| `chevron-right` / `chevron-left` / `chevron-down` | chevrons | Rows, back, expanders |
| `close` | × | Leave workout, close sheet |
| `more` | three dots | Overflow menu |
| `edit` / `trash` | pencil / bin | Exercise actions |
| `link` | chain | Share links |
| `gym` | building with a plate | My gym |
| `lock` / `logout` | padlock / door-arrow | Security |
| `moon` | crescent | Rest / no-gym day |
| `muscle` | flexed arm outline | Trained day (replaces 💪) |

### 1.6 Brand mark

The wordmark is "FITLOG" in uppercase, `font-display` 900 italic, -0.02em, with a volt underline bar (4px, `--r-pill`) under "FIT". It is pure CSS/HTML (`.wordmark`, `.wordmark-bar`), so no image is needed. The app icon (`static/icons/icon.svg` and the 180/192/512 PNGs) is an original volt bolt on `#0A0A0B`, echoing the Today tab glyph. The PNGs are full-bleed (the OS applies its own mask) and are marked `any maskable` in the manifest.

---

## 2. Navigation

### 2.1 Bottom tab bar: four tabs

| Tab | Label | Icon | Lands on | Also active for endpoints |
|---|---|---|---|---|
| 1 | **Today** | `today` | `workouts.today` | `workouts.session_view` (when not in focus mode) |
| 2 | **Library** | `library` | `workouts.exercises` | `workouts.exercise_*`, `workouts.exercise_ai*`, `workouts.gym_profile`, new `workouts.exercise_detail` |
| 3 | **Progress** | `progress` | new `workouts.progress` | `workouts.history`, `sharing.create` / created page |
| 4 | **Profile** | `profile` | `auth.settings` | `auth.*`, `integrations.*`, `sharing.manage` |

* Four tabs instead of five give bigger targets and calmer chrome. **Sync moves under Profile** as a "Devices" row with a status dot. A red dot appears on the Profile tab icon (`.tab-dot`) when an integration has `last_error`.
* Tab item: 64px tall plus the safe area. The icon is 24px above a 11px/600 label (an exception to the 12px floor that matches the iOS tab bar convention; labels stay `--text-2` at 7:1). Active state: `--accent-ink` icon and label, plus a 20 × 3px volt pill above the icon (`.tabbar-item.is-active::before`). Use `aria-current="page"` on the active tab.
* The tab bar is translucent with backdrop blur and a top hairline in `--line`.
* **Hidden** on: login, register, the public share page, error pages for anonymous users, and the **active workout focus mode** (section 3.3).

### 2.2 "Now bar" (workout in progress)

When the user has an unfinished session and is on any other signed-in page, a 56px bar (`.now-bar`) docks directly above the tab bar. It shows a pulsing volt dot, the workout name, the elapsed time (`data-elapsed-since="<ISO UTC>"`, ticked by JS) and a **Resume** chevron. Tapping it opens `workouts.session_view`. This keeps the active session one tap away from anywhere.

### 2.3 Page header

* No sticky brand top bar any more (minimal chrome). Each page starts with a **large title block** (`.page-head`): an eyebrow line (e.g. date) above a `.t-display` title, with up to two 44px `.btn-icon` actions on the right.
* Sub-pages (exercise detail, edit, AI preview, gym, share links, created share) use a compact header (`.page-head.is-sub`): a back chevron link (to the parent route, not `history.back()`), a `.t-headline` title centred, and an optional action. Its top padding is `env(safe-area-inset-top) + 8px`.
* Flash messages render as **toasts** (`.toast`, `role="status"`) pinned under the safe area at the top. They auto-dismiss after 4s via JS (errors stay until tapped) and remain in normal page flow when JS is off.

### 2.4 Reachability map (every existing feature keeps a path)

| Feature | Path |
|---|---|
| Start / continue workout | Today hero, Now bar |
| Log exercise, finish, delete session | Active workout |
| Calories (refresh, Apple Shortcut, manual) | Workout summary |
| Exercises: add, edit, delete, video | Library, then Exercise detail |
| AI builder, gym profile | Library header actions and the "Build with AI" card |
| History, share a day | Progress, then calendar day or "All days" |
| Share links (manage/revoke) | Profile, then Sharing |
| Oura, Apple tokens | Profile, then Devices (Sync page) |
| Settings, password, sign out (everywhere), delete account | Profile |

---

## 3. Screens

ASCII wireframes are at 390px width. `[ ]` is a button, `( )` is a check, `‹ ›` is a chip, and `▮` is the accent.

### 3.1 Login / Register (`auth/login.html`, `auth/register.html`)

```
┌──────────────────────────────┐
│                              │
│  FITLOG                      │  .wordmark (40px)
│  ▮▮▮                         │
│  Train smarter.              │  .t-display
│  Every set counts.           │
│  Your plan, your recovery,   │  .t-callout --text-2
│  your progress.              │
│                              │
│ ┌──────────────────────────┐ │
│ │ [G] Continue with Google │ │  .btn-google (white, 52px)
│ └──────────────────────────┘ │
│ ───────────── or ─────────── │  .divider
│ USERNAME                     │  .eyebrow label
│ [______________________]     │  16px input, 52px tall
│ PASSWORD                     │
│ [______________________]     │
│ ( ) Keep me signed in        │  .check (44px row)
│ [        Sign in         ]   │  .btn .btn-lg .btn-block (volt)
│ New here? Create an account  │  --accent-ink link
└──────────────────────────────┘
```

* `body.auth-page` uses a full-bleed `--bg` with a decorative CSS radial volt glow top-right (`.auth-hero::before`). There is no card border, so the form sits directly on the background.
* Register uses the same frame with title "Create your account" and fields username, name, password (hint: "At least 10 characters"), confirm.
* Errors appear inline under each field in `--danger` with an `alert-circle`-style icon, and the input border turns `--danger`.

### 3.2 Today (`workouts/today.html`), the home screen

```
┌──────────────────────────────┐
│ TUESDAY, OCT 21          (⚙) │  eyebrow + .btn-icon to Profile
│ Today                        │  .t-display
│                              │
│ ┌────────── HERO ──────────┐ │  .card.card-hero
│ │ TODAY'S WORKOUT   ‹✦ AI› │ │  eyebrow + .chip-ai
│ │ Push · Upper Strength    │ │  .t-display (title)
│ │ Chest: 100% recovered, … │ │  .hero-sub rationale, 2 lines max (line-clamp)
│ │ ‹Chest› ‹Shoulders› ‹Tri›│ │  .chip-row.chip-row-wrap (focus, max 4)
│ │ ┌──────┬──────┬───────┐  │ │  .stat-strip (3 inline stats)
│ │ │ 50   │  6   │ 18    │  │ │
│ │ │ MIN  │ EXER │ SETS  │  │ │
│ │ └──────┴──────┴───────┘  │ │
│ │ [ ▶  Start workout     ] │ │  .hero-cta right after the stats: above the
│ │                          │ │  fold (≈ y 500 on a 390 × 664 viewport)
│ │ Bench Press   4 × 6 @ 155│ │  .plan-list preview (first 3)
│ │ Incline DB    3 × 10 @ 50│ │
│ │ Cable Fly     3 × 12 @ 30│ │
│ │ + 3 more                 │ │
│ │ ▸ Change today's plan    │ │  <details class="hero-more">
│ │   Start empty workout    │ │  .btn-ghost
│ └──────────────────────────┘ │
│                              │
│ RECOVERY              Info › │  .section-head
│ ┌──────────────────────────┐ │  .card
│ │  [front fig] [back fig]  │ │  .body-map (section 5)
│ │ Fatigued ▮▮▮▮▮ Fresh  ○ NT│ │  .bm-legend: 5-step scale rec-0…rec-4 + Not trained
│ │ Fresh: legs, back, core  │ │  .t-callout, text summary
│ │ Recovering: chest 45%    │ │
│ └──────────────────────────┘ │
│                              │
│ THIS WEEK       ‹3-week streak›│  streak pill (.chip-accent + flame icon)
│ ┌──────────────────────────┐ │
│ │ M  T  W  T  F  S  S      │ │  .week-strip
│ │ ●  ●  ◎  ○  ○  ○  ○      │ │  ● gym  ◎ today  ○ future
│ └──────────────────────────┘ │
│ ┌────────┬────────┬────────┐ │  .stat-strip of .stat-tile
│ │ 412    │ 286    │ 2      │ │
│ │ ACTIVE │ WORKOUT│ SESS.  │ │
│ │ KCAL   │ KCAL   │        │ │
│ │ ⌚Apple │        │        │ │  source caption with icon
│ └────────┴────────┴────────┘ │
│                              │
│ TODAY'S SESSIONS             │  .list-group (existing sessions list)
│ 07:02–07:58 · 6 ex · 312 kcal›│
│                              │
│ [ ⤴ Share today ]            │  .btn-secondary .btn-block
└──────────────────────────────┘
   [Today] [Library] [Progress] [Profile]
```

**Hero card states.** The engineer renders one of these. Each state has exactly one primary button.

| State | Eyebrow / title | Primary action |
|---|---|---|
| `plan` (AI or coach fallback) | TODAY'S WORKOUT / plan title | **Start workout** (POST `plan_start`). A `<details>` "Change today's plan" holds `plan_form` with **Build with AI coach** (`formaction=plan_generate`, only when AI is on, `data-busy="Planning your session…"`) and **Rebuild without AI** (`plan_rebuild`). Ghost "Start empty workout" (`session_start`) |
| `active` | IN PROGRESS / plan title + elapsed + "3 of 6 exercises" `.bar` | **Continue workout** |
| `done` | DONE TODAY / "Great work" + minutes + kcal | **View summary**; ghost "Start another" (`session_start`) |
| `rest` (`plan.kind == "recovery"`) | REST DAY / "Your muscles need another day" + `moon` icon | **Start anyway** (`plan_start`, secondary styling) |

There is no "no plan yet" state: `GET /` always yields a plan (an unsaved coach fallback when nothing is stored), and `GET /` never calls the AI. State rules are in contract 4.2.

* The muscle-focus chips use the recovery colour dot of each muscle (`.chip .dot.rec-3`) so the hero explains *why* these muscles were picked.
* Plan preview rows are `.plan-row` items: name, then `sets × reps @ weight` right-aligned in `.num`.
* The AI is untrusted: all plan text is autoescaped, and weights and reps are rendered from validated numeric fields only.

### 3.3 Active workout (`workouts/session.html` while `ended_at is None`), focus mode

`body.focus-mode` hides the tab bar and Now bar. The header is sticky.

```
┌──────────────────────────────┐
│ (×)   Push · Upper    [Finish]│ .focus-head (sticky, blurred bg)
│       24:13 ▮▮▯▯▯▯ 5/18      │ elapsed .num + .bar (SETS done / planned)
├──────────────────────────────┤
│ ┌──────────────────────────┐ │ .ex-card.is-current
│ │[thumb] Bench Press   (▶)(⇄)(⋯)│ .ex-card-head: thumb 56px, icons 44px
│ │ ‹Chest› Barbell · e1RM 185│ │ .ex-meta
│ │                          │ │
│ │ SET  TARGET   LB    REPS  ✓ │ .set-table header (.eyebrow)
│ │ ┌────────────────────────┐ │
│ │ │ 1  135×10 [135] [10] (✓)│ │ .set-row.is-done (dimmed target, volt ✓)
│ │ │    How did it feel?     │ │ .rate (shown after ✓, optional)
│ │ │    ‹Easy› ‹Good› ‹Hard› │ │
│ │ ├────────────────────────┤ │
│ │ │ 2  145×8  [145] [ 8] ( )│ │ .set-row.is-current (volt left bar)
│ │ ├────────────────────────┤ │
│ │ │ 3  145×8  [145] [ 8] ( )│ │ .set-row
│ │ └────────────────────────┘ │
│ │ [+ Add set]   Rest 2:00 ⏱│ │ .btn-ghost + rest setting
│ │ ▸ How to (video + notes)  │ │ <details class="ex-howto"> with video macro
│ └──────────────────────────┘ │
│ ┌──────────────────────────┐ │ .ex-card; body in <details class="ex-body">
│ │ Incline DB Press   0/3 sets│ │ name + sets done; open only when current
│ │ 3 × 10 @ 50 lb          ⌄ │ │ summary.ex-body-sum (hidden when open)
│ └──────────────────────────┘ │
│ ...                          │
│ [ + Add exercise ]           │ .btn-secondary .btn-block → sheet
│ Delete workout               │ .btn-ghost danger, data-confirm
├──────────────────────────────┤
│ (⏱ ring) 1:24  REST  [-15][+15][Skip]│ .rest-timer (fixed bottom, --shadow-2)
└──────────────────────────────┘
```

* **Set row** (`.set-row`) is a grid `32px 1fr 76px 64px 52px`: index, target ("135 × 10", `--text-2`), weight input, reps input, check button. The row is at least 56px tall. Inputs are `inputmode="decimal"` (weight) / `"numeric"` (reps), 16px font and `--surface-2`.
  * Inputs are **pre-filled with the prescription** so the common path is one tap on ✓.
  * ✓ is a 44px round button: an outline circle that becomes a volt fill with a `check` icon and `--on-accent` colour. It sets `aria-pressed` and `aria-label="Complete set 2"`.
  * Done row: `.is-done` dims the target text and the row background becomes `--surface-2`.
  * The current row (first not done) gets `.is-current` with a 3px volt inset bar on the left.
* **Each set row is a `<form method="post">`** with CSRF. A `SetLog` row exists only for a completed set: checking posts `set_index` to `POST /sessions/<sid>/entries/<eid>/sets`; un-checking posts to `POST /sessions/<sid>/sets/<set_id>/delete`. Targets come from the entry's `EntryPlan`, and the unit is the entry's own `EntryPlan.unit` (never from the form; the weight column is labelled with it). Progressive enhancement: `workout.js` intercepts submit (`data-set-form`), `fetch`es same-origin with `X-Requested-With: fetch`, applies the JSON (contract 4.1) and swaps the row state and the form's `action`. Without JS (or on a non-JSON reply) it is a normal POST and redirect to `#ex-<entry_id>`.
* **Easy / Good / Hard** (`.rate`, a radio-chip group in its own small form, `POST /sessions/<sid>/sets/<set_id>/rate`) appears inside the row after it is checked. Stored values are `easy` / `ok` / `hard`; `ok` is labelled **Good** (class `.rate-good`). With JS a radio change saves immediately; without JS a visible "Save" button submits it. Selected chip colours: Easy uses `--ok`, Good uses `--surface-3`, Hard uses `--warn` (text on `-soft` backgrounds). Labels always include the word, not only colour.
* **Rest timer** (`.rest-timer`) starts automatically when a set is checked, using the exercise's `rest_seconds` (`data-rest="120"`). It is a fixed bottom bar, 76px plus the safe area, containing: an SVG ring (stroke-dashoffset updated by JS via `setAttribute`), the remaining time as `.t-title .num`, "REST" as an eyebrow, and `−15` / `+15` / `Skip` buttons at 44px. At 0 it shows "Go — set 3" in volt, calls `navigator.vibrate(200)` where supported, and collapses after 3s. It updates an `aria-live="polite"` region only at 30s, 10s and 0 (not every second). Without JS the bar is not rendered (`hidden` until JS removes it).
* **Collapsed cards.** In focus mode a tracked card's note, set table, Add set row and How to sit in `<details class="ex-body">`, open server-side only for the current card (or one with some sets done). When the last set of an exercise is checked, `workout.js` opens the next card and scrolls it into view (no smooth scroll under reduced motion). Without JS the redirect to `#ex-<id>` still lands on open rows.
* **Swap** (`swap` icon, current card only; other cards offer "Swap exercise" in the ⋯ menu so their names keep one line) links to the page `swap.html` (`GET /sessions/<sid>/entries/<eid>/swap`). It is offered **only while the entry has no logged sets** (JS hides the icon once a set is checked). The page lists up to 3 options (coach fallback, or AI when asked with a reason), each a POST "Use this" button to `…/swap/<choice>`, plus a reason form to ask for new options. Plan-level swaps are phase 2.
* **Video** (`video` icon) toggles the `<details class="ex-howto">` holding the existing `video()` macro (youtube-nocookie, sandboxed, lazy) and instructions. If there is no video id, it shows the existing "Find a how-to video on YouTube" link.
* **More** (`⋯`): Swap exercise (non-current cards, while no set is logged), Remove exercise (POST `entry_delete`), Edit exercise in library.
* **Thumbnails**: a YouTube thumbnail that fails to load is replaced by `app.js` with the empty dumbbell tile (`.ex-thumb-empty`); no inline `onerror`.
* **Add exercise**: the existing `EntryForm` (select + sets/reps/weight) inside a sheet or `<details>`. It creates the entry and its set rows.
* **Finish** (header, volt `.btn-sm`) does a POST to `session_finish` with `data-confirm="2 sets not logged. Finish anyway?"`. The confirm text is generated only when sets remain.
* **(×)** returns to Today and does **not** end the session (the Now bar appears).

### 3.4 Workout summary (`workouts/session.html` when `ended_at` is set)

```
┌──────────────────────────────┐
│ ‹ Today                 (⋯)  │ .page-head.is-sub (⋯: delete workout)
│ WORKOUT COMPLETE             │ eyebrow --ok
│ Push · Upper                 │ .t-display
│ Tue Oct 21 · 07:02–07:58     │ .t-footnote
│ ┌──────────────────────────┐ │ .card.card-hero
│ │ 🔥 312          ⌚ Apple  │ │ .t-hero kcal + .chip source
│ │ ACTIVE KCAL              │ │
│ │ ┌──────┬──────┬───────┐  │ │ .stat-strip
│ │ │ 56   │ 9,840│  18   │  │ │
│ │ │ MIN  │ LB VOL│ SETS │  │ │
│ │ └──────┴──────┴───────┘  │ │
│ │ [↻ Refresh][⌚ Send from Watch]│ .kcal-actions: two equal-width buttons
│ │ ▸ Enter calories manually │ │ <details> existing ManualCaloriesForm
│ └──────────────────────────┘ │
│ [ ⤴ Share with a friend ]    │ .btn .btn-lg .btn-block (volt), the one action
│ PERSONAL RECORDS             │
│ 🏆 Bench Press  e1RM 192 (+7)│ .pr-row (trophy icon --ok)
│ MUSCLES WORKED               │
│ [mini body map, .is-target]  │ .body-map.is-mini
│ HOW IT FELT                  │
│ Easy 6 · Good 9 · Hard 3     │ .rate-summary (3 bars)
│ NEXT TIME                    │
│ Bench 4 × 6 @ 160  ▲ +5      │ .plan-row with .delta-up
│ EXERCISES                    │ entries + DONE sets only; untouched → "Skipped"
│ Delete workout               │ .btn-ghost danger
└──────────────────────────────┘
```

* Calories source label: "Apple Watch" / "Oura" / "Manual", shown as a chip with the `watch` / `ring` / `edit` icon. When null the hero shows "Calories pending" (`.t-title`, `--text-2`) and "Waiting for your watch…" instead of a big dash, and the finish toast is just "Workout saved.".
* Volume is the sum of weight × reps over done sets, shown in the user's unit.
* PRs come from new e1RM records (Epley: `w × (1 + reps/30)`, reps ≤ 12). If there are none, the section is hidden.

### 3.5 Exercise library (`workouts/exercises.html`)

```
┌──────────────────────────────┐
│ YOUR EXERCISES        (+)(⚙) │ (+) new manual, (gym) My gym
│ Library                      │
│ [🔍 Search exercises      ]  │ .search (JS filter, data-filter)
│ ‹All› ‹Chest› ‹Back› ‹Legs›… │ .chip-row scrollable; links ?muscle=
│ ┌──────────────────────────┐ │ .card.card-ai-cta
│ │ ✦ Build with AI          │ │ --ai-soft bg, sparkle icon
│ │ A workout for your gym   │ │
│ │ and equipment         ›  │ │
│ └──────────────────────────┘ │
│ CHEST · 4                    │ .list-section-head (grouped by muscle)
│ ┌──────────────────────────┐ │ .list-group
│ │[thumb] Bench Press  ‹AI››│ │ .list-row: 56px thumb, title, meta
│ │        4 × 6 · e1RM 185  │ │
│ ├──────────────────────────┤ │
│ │[thumb] Cable Fly        ›│ │
│ └──────────────────────────┘ │
└──────────────────────────────┘
```

* Thumbnail: `https://i.ytimg.com/vi/<id>/mqdefault.jpg` (already allowed in CSP `img-src`), `object-fit: cover`, `--r-sm`, `alt=""`, `loading="lazy"`. If there is no video, use `--surface-2` with the `muscle` icon.
* Rows link to the **new exercise detail** page. Edit and delete move there.
* Empty state (`.empty-state`): `library` icon (xl), "Your library is empty", and two buttons: "Build with AI" (`.btn-ai`) and "Add manually" (`.btn-secondary`).

**Exercise detail** (new `GET /exercises/<id>`, loaded via `owned_or_404`):

```
‹ Library            (✎)(🗑)
[ 16:9 video (existing macro) ]
Bench Press                   .t-display
‹Chest› ‹Triceps› Barbell ‹AI›
┌ stat-strip: e1RM 185 · BEST 155×8 · LAST Oct 18 ┐
[mini body map with .is-target regions]
HOW TO        (instructions, .pre)
TREND         SVG sparkline of e1RM (last 12 sessions)
HISTORY       date — sets list
DEFAULTS      4 × 6 · rest 120s
```

Delete uses the existing POST plus `data-confirm`.

### 3.6 AI builder (`workouts/ai_form.html`, `workouts/ai_preview.html`)

```
‹ Library
✦ AI BUILDER                   eyebrow --ai
Build a workout               .t-display
┌ card: YOUR GYM ─────────────────┐
│ Commercial · barbell, dumbbells… │
│ Goal: strength · intermediate  ›│ → gym_profile
└──────────────────────────────────┘
FOCUS
‹Full body› ‹Upper› ‹Lower› ‹Push› ‹Pull› ‹Legs› ‹Core›   .chip-radio group (wraps)
EXERCISES        MINUTES
[ − ]  6  [ + ]  [ − ] 45 [ + ]   .stepper (number input + JS ± buttons)
ANYTHING ELSE?
[ e.g. go easy on my left shoulder ]  textarea, 300 max, counter
[ ✦ Build my workout ]          .btn-ai .btn-lg .btn-block, data-busy
```

* `.chip-radio` puts real `<input type="radio">` elements (visually hidden, focusable) behind labels. The selected state is `--accent-soft` with an `--accent-ink` border. It is the same `FOCUS` select field rendered as radios, so no form-logic change is needed (or a `RadioField` with the same choices).
* While building: the button reads "Building your workout…" and below it 3 `.skeleton` cards shimmer (static when reduced motion is set).
* If AI is disabled: the `.flash-info` toast text stays and the button is disabled.
* **Preview**: each exercise is a `.card` with the checkbox as a 28px round check in the top-right (`.check-round`), the name `.t-headline`, `sets × reps · rest 90s` and a muscle chip, and instructions collapsed in `<details>` ("How to"). Sticky footer `.sticky-actions`: "Save 5 to my library" (count updated by JS) and a ghost "Try again".

### 3.7 Progress (new `workouts.progress`; `workouts.history` kept as "All days")

```
┌──────────────────────────────┐
│ LAST 12 WEEKS                │
│ Progress                     │
│ ┌──────────────────────────┐ │ .card.card-hero
│ │   ╭───────╮              │ │ .ring (160px) value 0–100
│ │   │  64   │  STRENGTH    │ │ .t-hero inside ring
│ │   │       │  INDEX       │ │
│ │   ╰───────╯  100 = you   │ │ self-relative, no level labels
│ │  ▲ +3 in 4 weeks         │ │ .delta-up
│ └──────────────────────────┘ │
│ BY MUSCLE                    │
│ Chest      ▮▮▮▮▮▮▮▯▯▯  68  │ .meter-row (label, .bar, value)
│ Back       ▮▮▮▮▮▮▯▯▯▯  61  │
│ Shoulders  ▮▮▮▮▮▯▯▯▯▯  52  │
│ … (all 11 groups; "—" if none logged)
│ TOP LIFTS                    │
│ Bench Press  e1RM 185  ›     │ .list-group → exercise detail
│ CALENDAR        ‹ Oct 2026 › │ .cal-head (prev/next = links ?month=)
│ M  T  W  T  F  S  S          │
│ ●  ●  ○  ●  ·  ·  ·          │ .cal-grid
│ ...                          │
│ ● Gym  ○ No gym  ◎ Today     │ legend
│ 14 gym days · 3-week streak  │
│ [ All days › ]               │ → workouts.history (existing list)
└──────────────────────────────┘
```

* **Strength Index** (FitLog's own name; avoid other apps' terms) is **self-relative**: 100 is your own baseline for each lift (AI_COACH §5), the overall index is the mean of the trained groups, and there are **no level labels** and no bodyweight standards. The ring and the per-muscle bars fill with `index_pct = clamp(round(index × 2 / 3), 0, 100)`, so a baseline of 100 fills about two thirds and 150+ fills the ring. Muscles with no data show "—". The index-over-time sparkline is phase 2 (the e1RM sparkline on exercise detail stays).
* **Calendar day cell** (`.cal-day`) is a square link to the single-day view `history?day=YYYY-MM-DD`; `?month=YYYY-MM` on Progress pages the calendar:
  * `.is-gym` uses a volt fill and `--on-accent` number.
  * `.is-rest` (past, no gym) has a `--line` outline and `--text-3` number, plus a small `moon` dot when explicitly shared as a no-gym day.
  * `.is-today` gets a 2px `--accent-line` ring.
  * `.is-future` is `--text-3` and not a link.
  * Each cell's `aria-label` reads e.g. "Oct 14, gym day, 312 kcal".
* **History ("All days")** reuses the existing 14-day list restyled. Each day is a `.card` with a date `.t-headline`, a status chip ("Gym" `--ok` with `check` icon, or "No gym" `--danger` with `moon` icon; text always present), the `_day_card` stat strip, exercise names, "View session" rows and **Share this day** (`.btn-secondary .btn-sm`, `share` icon).

### 3.8 Sync / Devices (`integrations/index.html`, under Profile)

```
‹ Profile
Devices                          .t-display
┌ card ─────────────────────────────┐
│ (watch) Apple Health & Watch      │ .device-card-head (icon tile 44px)
│ ● 2 tokens · last used Oct 21 7:58│ status line (.dot ok/na)
│ ▸ How to set up (4 steps)         │ <details> numbered .steps
│ Daily   …/api/v1/apple/daily  [⧉] │ .copy-row code + copy
│ Workout …/api/v1/apple/workout[⧉] │
│ [ name: iPhone Shortcut ][Create] │ token form
│ iPhone Shortcut  ab12…  used 2h  [Revoke] │ .list-row
└───────────────────────────────────┘
┌ card ─────────────────────────────┐
│ (ring) Oura Ring          ‹Connected›│ chip --ok / ‹Not connected›
│ Last sync Oct 21, 7:40 AM         │
│ (error text in --danger if any)   │
│ [↻ Sync now]   Disconnect         │ .btn-secondary + .btn-ghost danger
└───────────────────────────────────┘
```

* The new-token panel keeps "Copy this token now — it won't be shown again." in a `.card` with a `--warn` left border, a monospace read-only input and a Copy button.
* Not configured on server: `--text-2` explanation, no button.

### 3.9 Profile & Settings (`auth/settings.html`)

```
Profile                              .t-display
┌───────────────────────────────┐
│ (JD)  Jonas D.                │ .avatar (initials, volt-on-dark), .t-title
│       @jonas · Google linked  │
└───────────────────────────────┘
TRAINING                              .list-group with rows (chevron)
  (gym) My gym              Commercial ›
  (⚖)  Units & session length  lb · 45 min ˅  <details> TrainerSettingsForm
DEVICES
  (watch) Apple Health      ● Active  ›   → integrations.index
  (ring)  Oura              ● Off     ›
SHARING
  (link) Share links         2 active ›   → sharing.manage
ACCOUNT
  ▸ Profile (name, timezone + "Use my phone's timezone")   <details> with existing form
  ▸ Password                                                <details> existing pw form
  ▸ Sign-in methods
  [ Sign out ]  [ Sign out on all devices ]                 .btn-secondary
DANGER ZONE                                                 .card.danger-zone
  Delete account (type username) [ Delete my account ]      .btn-danger
```

* Grouped list rows (`.list-row`) are at least 52px tall: a leading icon tile, title, a trailing value in `--text-2` and a chevron.
* Each existing form keeps its action URL, CSRF and fields. Only presentation changes.

### 3.10 Public share page (`sharing/public.html`)

There is no tab bar and no account chrome. It is designed to look good as a screenshot.

```
┌──────────────────────────────┐
│ TUESDAY, OCTOBER 21          │ eyebrow
│ (dumbbell) Jonas trained today│ .t-display; icon tile --accent
│ ┌──────────────────────────┐ │ .card.card-hero
│ │ 312   │ 640   │ 6        │ │ .stat-strip
│ │WORKOUT│ ACTIVE│ EXERCISES│ │
│ │ KCAL  │ KCAL  │          │ │
│ └──────────────────────────┘ │
│ EXERCISES                    │
│ Bench Press   4 × 6 @ 155    │ .plan-row list
│ ...                          │
│      FITLOG · shared with you│ .wordmark small, --text-3
└──────────────────────────────┘
```

* Only exercises that recorded training are counted and listed (`DaySummary.logged_entries`). If a day counts as a gym day but nothing was logged (finished with calories), the planned rows are shown dimmed under a "Planned" eyebrow.
* No-gym variant: `moon` icon tile in `--surface-3`, title "Jonas didn't go to the gym", subline "Rest day or skipped — give them a nudge!", and stats shown dimmed. The page still shows only the existing permitted fields (B7 in SECURITY.md). **Do not add** the recovery map, strength index or other days here.
* `share/created.html`: a big copy row plus a primary "Send to a friend" (`data-share-url`, `share` icon) and a hint with the expiry date.
* `share/manage.html`: a `.list-group` of links with a "Revoke" `.btn-ghost` danger action.

### 3.11 Error page

A centred `.empty-state`: the `.t-hero` code (404/500) in `--text-3`, the title, the message and a "Go home" `.btn`.

---

## 4. Component catalogue (class names)

The existing class names `.btn`, `.btn-secondary`, `.btn-danger`, `.btn-ai`, `.btn-small`, `.btn-block`, `.card`, `.field`, `.check`, `.video`, `.badge`, `.flash-*`, `.stats` / `.stat` **stay valid**, restyled with the new tokens, so templates can migrate screen by screen. New components use the same single-hyphen naming.

| Component | Classes | Notes |
|---|---|---|
| Page head | `.page-head`, `.page-head.is-sub`, `.page-eyebrow`, `.page-title`, `.page-actions` | Large title / compact sub-page header |
| Focus header | `.focus-head`, `.focus-title`, `.focus-elapsed` | Sticky, blurred, active workout only |
| Tab bar | `.tabbar`, `.tabbar-item`, `.tabbar-item.is-active`, `.tab-dot` | `aria-current="page"` |
| Now bar | `.now-bar`, `.now-bar-dot`, `.now-bar-time` | Docked above tab bar |
| Section head | `.section-head` (eyebrow + optional link) | |
| Card | `.card`, `.card-hero`, `.card-flat` (no border, `--surface-2`), `.card-ai-cta`, `.card-link` (whole card tappable) | |
| Buttons | `.btn` (volt primary), `.btn-secondary`, `.btn-ghost`, `.btn-danger`, `.btn-ai`, `.btn-lg` (56px), `.btn-sm` (=`.btn-small`, 40px visual / 44px hit), `.btn-block`, `.btn-icon` (44px round, `--surface-2`) | Primary text `--on-accent`, weight 700 |
| Chips | `.chip`, `.chip-accent`, `.chip-ai`, `.chip-ok`, `.chip-warn`, `.chip-danger`, `.chip-row` (horizontal scroll, scroll-snap), `.chip .dot` | 32px tall visual, 44px tap area via padding/`::after` |
| Choice chips | `.chip-radio` (radio/checkbox + label) | Focus ring visible on the label |
| Stat tile | `.stat-tile`, `.stat-tile-value` (`.num`), `.stat-tile-unit`, `.stat-tile-label` (eyebrow), `.stat-tile-src` | |
| Stat strip | `.stat-strip` (3-col grid; 2-col under 360px) | |
| Lists | `.list-group`, `.list-row`, `.list-row-lead`, `.list-row-body`, `.list-row-title`, `.list-row-meta`, `.list-row-trail` | iOS grouped style |
| Plan row | `.plan-row`, `.plan-row-name`, `.plan-row-rx` (`.num`), `.delta-up`, `.delta-down` | sets × reps @ weight |
| Exercise card | `.ex-card`, `.ex-card.is-current`, `.ex-card-head`, `.ex-thumb`, `.ex-meta`, `.ex-howto` | |
| Set table | `.set-table`, `.set-head`, `.set-row`, `.set-row.is-done`, `.set-row.is-current`, `.set-idx`, `.set-target`, `.set-input`, `.set-check` | Each row is a form |
| Rating | `.rate`, `.rate-easy`, `.rate-good`, `.rate-hard`, `.rate-summary` | Radio chips |
| Rest timer | `.rest-timer`, `.rest-timer-ring`, `.rest-timer-time`, `.rest-timer-label`, `.rest-timer-actions`, `.rest-timer.is-done` | `hidden` until JS |
| Ring | `.ring`, `.ring-track`, `.ring-fill`, `.ring-label` | SVG, see section 8 |
| Bar | `.bar`, `.bar-track`, `.bar-fill`, `.meter-row` | SVG, see section 8 |
| Sparkline | `.spark`, `.spark-line`, `.spark-area` | SVG `polyline points` from server |
| Body map | `.body-map`, `.body-map.is-mini`, `.bm-figure`, `.bm-body`, `.mm`, `.mm-<group>`, `.rec-0`…`.rec-4`, `.rec-na`, `.is-target`, `.bm-legend` | Section 5 |
| Week strip | `.week-strip`, `.week-day`, `.week-day.is-gym`, `.is-today`, `.is-future` | |
| Calendar | `.cal`, `.cal-head`, `.cal-grid`, `.cal-dow`, `.cal-day`, `.is-gym`, `.is-rest`, `.is-today`, `.is-future`, `.cal-legend` | |
| Stepper | `.stepper`, `.stepper-btn` | `data-stepper`; number input still works without JS |
| Search | `.search` | `data-filter="#exercise-list"`; filters `[data-name]` rows |
| Sheet | `dialog.sheet`, `.sheet-handle`, `.sheet-body` | Optional (phase 2). Native `<dialog>`, opened by `data-sheet-open="#id"`; every sheet must also work as `<details>` or a page |
| Toast | `.toast`, `.toast-success`, `.toast-error`, `.toast-info` | Replaces `.flash` visuals |
| Empty state | `.empty-state`, `.empty-state-icon` | |
| Skeleton | `.skeleton`, `.skeleton-line` | Shimmer disabled for reduced motion |
| PR | `.pr-row` | trophy icon in `--ok` |
| Avatar | `.avatar` | Initials, 56px |
| Device | `.device-card-head`, `.icon-tile` | 44px rounded square holding an icon |
| Wordmark | `.wordmark`, `.wordmark-bar` | |
| Utilities | `.num`, `.eyebrow`, `.t-*`, `.muted` (= `--text-2`), `.visually-hidden`, `.stack-2/3/4` (vertical gap), `.row` / `.row-between` | |

### Jinja macros to add

`app/templates/_ui.html` (and `_icons.html` for `icon()`):

* `icon(name, cls="")`
* `stat_tile(value, label, unit=None, src=None)`
* `chip(text, kind="", dot=None)`
* `ring(pct, size=160, label=None)` and `bar(pct)`, which clamp `pct` with `|int` and `max(0, min(100, …))` before emitting attributes
* `body_map(recovery: dict[str, int|None], mode="recovery"|"target", targets=())`
* `set_row(entry, set, ws)`
* `plan_row(name, sets, reps, weight, unit, delta=None)`
* `week_strip(days)`, `calendar(month_days)`
* Existing `video`, `post_button` and `share_button` stay. `share_button` swaps the emoji for `icon('share')`.

---

## 5. Muscle recovery body map

### 5.1 Canonical groups

`chest, back, shoulders, biceps, triceps, forearms, core, quads, hamstrings, glutes, calves` (11 keys, lowercase). They are used as class suffixes (`.mm-chest`), `data-muscle` values and dict keys.

Free-text `Exercise.muscle_group` (and AI `muscle_group`) is normalised by a service function `services/muscles.normalize(text) -> list[str]` with a synonym table. Examples: pecs → chest; lats/traps/upper back/lower back → back; delts → shoulders; abs/obliques → core; legs → quads + hamstrings + glutes + calves; arms → biceps + triceps; full body → all. Unknown text maps to `[]`. Templates **only ever emit classes from this fixed list**, never user text in a class attribute.

### 5.2 Figure

Two simplified, original geometric figures (front and back) sit side by side in **one inline SVG**, `viewBox="0 0 260 270"`. The front is at x 0–120 and the back group uses `transform="translate(140 0)"`. The figure is built from rounded rects and ellipses, so it is easy to maintain and has no traced artwork. Each region carries its classes plus a `<title>` for hover/long-press and assistive tech.

Starter markup (front figure; the back mirrors it with the substitutions listed below):

```html
<svg class="body-map" viewBox="0 0 260 270" role="img" aria-labelledby="bm-t bm-d">
  <title id="bm-t">Muscle recovery</title>
  <desc id="bm-d">Chest 45% recovered, back 100%, … </desc>   {# server-generated summary #}
  <g class="bm-figure bm-front">
    <!-- silhouette parts that are not muscle groups -->
    <circle class="bm-body" cx="60" cy="18" r="13"/>
    <rect   class="bm-body" x="54" y="30" width="12" height="9" rx="3"/>
    <rect   class="bm-body" x="43" y="113" width="34" height="14" rx="6"/>      <!-- pelvis -->
    <circle class="bm-body" cx="23" cy="129" r="5"/><circle class="bm-body" cx="97" cy="129" r="5"/>
    <rect   class="bm-body" x="43" y="236" width="13" height="8" rx="4"/><rect class="bm-body" x="64" y="236" width="13" height="8" rx="4"/>
    <!-- muscle regions -->
    <ellipse class="mm mm-shoulders rec-4" data-muscle="shoulders" cx="35" cy="49" rx="11" ry="9"><title>Shoulders 92%</title></ellipse>
    <ellipse class="mm mm-shoulders rec-4" data-muscle="shoulders" cx="85" cy="49" rx="11" ry="9"/>
    <rect class="mm mm-chest rec-2"    data-muscle="chest"    x="40" y="41" width="19" height="23" rx="7"><title>Chest 45%</title></rect>
    <rect class="mm mm-chest rec-2"    data-muscle="chest"    x="61" y="41" width="19" height="23" rx="7"/>
    <rect class="mm mm-biceps rec-3"   data-muscle="biceps"   x="22" y="60" width="11" height="27" rx="5"/>
    <rect class="mm mm-biceps rec-3"   data-muscle="biceps"   x="87" y="60" width="11" height="27" rx="5"/>
    <rect class="mm mm-forearms rec-4" data-muscle="forearms" x="18" y="90" width="11" height="33" rx="5"/>
    <rect class="mm mm-forearms rec-4" data-muscle="forearms" x="91" y="90" width="11" height="33" rx="5"/>
    <rect class="mm mm-core rec-4"     data-muscle="core"     x="45" y="67" width="30" height="45" rx="9"/>
    <rect class="mm mm-quads rec-4"    data-muscle="quads"    x="41" y="128" width="18" height="56" rx="8"/>
    <rect class="mm mm-quads rec-4"    data-muscle="quads"    x="61" y="128" width="18" height="56" rx="8"/>
    <rect class="mm mm-calves rec-4"   data-muscle="calves"   x="43" y="189" width="13" height="45" rx="6"/>
    <rect class="mm mm-calves rec-4"   data-muscle="calves"   x="64" y="189" width="13" height="45" rx="6"/>
  </g>
  <g class="bm-figure bm-back" transform="translate(140 0)">
    <!-- same head/neck/pelvis/hands/feet silhouette -->
    <!-- shoulders: same ellipses (rear delts) -->
    <!-- back:      x=40 y=40 w=40 h=64 rx=12  (traps + lats + lower back, one region) -->
    <!-- triceps:   same boxes as biceps -->
    <!-- forearms:  same as front -->
    <!-- glutes:    x=42 y=108 w=17 h=22 rx=9  and  x=61 y=108 w=17 h=22 rx=9 -->
    <!-- hamstrings:x=41 y=132 w=18 h=52 rx=8  and  x=61 y=132 w=18 h=52 rx=8 -->
    <!-- calves:    ellipses cx=49.5 cy=208 rx=7.5 ry=22 and cx=70.5 cy=208 rx=7.5 ry=22 -->
  </g>
  <text class="bm-caption" x="60" y="264" text-anchor="middle">Front</text>
  <text class="bm-caption" x="200" y="264" text-anchor="middle">Back</text>
</svg>
```

* Regions have a 1.5px gap so they read as separate. `.mm` gets `stroke: var(--bg); stroke-width: 1.5`.
* Size: 100% width up to 320px on Today and 140px wide `.is-mini` on summary and exercise detail.
* Not interactive in v1, so there is no tab stop per region. Phase 2: tapping a region filters Library by that muscle (wrap each region in an `<a>` with `aria-label`).

### 5.3 Colour scale

The server computes recovery per group with the AI_COACH §3.3 formula (0–100). A muscle not trained in the lookback is 100 / Fresh but coloured `rec-na`. `recovery.rec_class` maps it to a class, with buckets cut so the colour never contradicts the status word (Fresh ≥ 85, Recovering 50–84, Fatigued < 50):

| Class | Recovered | Label (text) | Dark | Light |
|---|---|---|---|---|
| `.rec-0` | < 25% | Fatigued | `--rec-0: #FF4D5E` | `#E11D48` |
| `.rec-1` | 25–49% | Fatigued | `--rec-1: #FF8A3D` | `#EA580C` |
| `.rec-2` | 50–69% | Recovering | `--rec-2: #FFC23D` | `#CA8A04` |
| `.rec-3` | 70–84% | Recovering | `--rec-3: #9BE36D` | `#65A30D` |
| `.rec-4` | ≥ 85% | Fresh | `--rec-4: #3DDC84` | `#15803D` |
| `.rec-na` | not trained recently | Fresh, not trained recently | `--rec-na: #2E2E34` | `#D9D9D3` |

```css
.mm { fill: var(--rec-na); stroke: var(--bg); stroke-width: 1.5; transition: fill var(--dur-3) var(--ease); }
.mm.rec-0 { fill: var(--rec-0); } /* … through .rec-4 */
.bm-body { fill: var(--surface-3); }
.body-map.is-target .mm { fill: var(--surface-3); }
.body-map.is-target .mm.is-target { fill: var(--accent-line); }   /* exercise detail & summary: "muscles worked" */
```

* Mapping is computed in Python (`RecoveryView.rec`); templates only ever emit classes from this fixed list.
* **Colour is never the only signal.** The card under the map lists groups by label: "Fresh: back, legs, core · Recovering: chest 45%, triceps 58% · Fatigued: shoulders 15%". The `<desc>` carries the full list for screen readers. The legend is a 5-step scale (`.bm-scale`, Fatigued → Fresh, every `.rec-*` colour the map can paint) plus "Not trained", and in the Fatigued / Recovering lists each muscle carries a dot in its own `.rec-*` colour, so a light-green 74% Quads matches its dot.
* Recovery model: the dose-based formula in AI_COACH §3.3 (7-day lookback, "Hard" ratings and secondary muscles weighted there), implemented in `services/recovery.py`. The UI only consumes `RecoveryPanel`.

---

## 6. Accessibility

* **Targets**: every interactive element is at least 44 × 44 CSS px (`min-height: 44px; min-width: 44px`). Small-looking chips and icon buttons extend the hit area with padding or a transparent `::after`. Adjacent targets have 8px or more of spacing.
* **Contrast**: WCAG 2.2 AA. Text is ≥ 4.5:1 (≥ 3:1 for 24px+ or 19px bold); UI graphics and focus rings are ≥ 3:1. The token table in section 1.2 is pre-checked. Volt is never used as text on light backgrounds; `--accent-ink` is used instead.
* **Focus**: `:focus-visible { outline: 3px solid var(--accent-ink); outline-offset: 2px; }` applies everywhere, including chip labels whose radio is focused (`.chip-radio input:focus-visible + label`). Never remove outlines without a replacement.
* **Inputs**: font-size is **16px minimum** (prevents iOS zoom). Each input has a visible label, using an `.eyebrow` label element linked by `for`. Set-row inputs carry `aria-label="Set 2 weight (lb)"` / `"Set 2 reps"`. Use the right `inputmode` and `autocomplete`.
* **Reduced motion**: `@media (prefers-reduced-motion: reduce)` sets all transitions and animations to `0.01ms`, turns off the skeleton shimmer, the now-bar pulse and the ring fill animation, and disables `scroll-behavior: smooth`. The rest timer still counts but without the ring sweep animation (it jumps per second).
* **Live regions**: toasts use `role="status"` (errors use `role="alert"`). The rest timer announces only at 30s, 10s and done. Set completion updates the "2/6" progress text, which sits in an `aria-live="polite"` span.
* **Semantics**: one `<h1>` per page (the large title). Sections use `<h2>` styled as `.eyebrow`. Use `<nav aria-label="Main">` for the tab bar, `aria-current="page"` on the active tab, `<button>` for actions and `<a>` for navigation. Real `<input type=radio>` elements sit behind Easy/Good/Hard and the focus chips. Use `<dialog>` for sheets (focus is trapped natively, and Esc closes it).
* **Icons**: decorative SVGs get `aria-hidden="true"`. Icon-only buttons get `aria-label`. Status is never conveyed by icon or colour alone (Gym / No gym chips keep their words).
* **Dynamic Type / zoom**: layouts must survive 200% text zoom without horizontal scroll. The set-row grid wraps the target under the index at narrow widths or large text (`@container` or `@media (max-width: 360px)`).
* **Safe areas**: `env(safe-area-inset-*)` on the tab bar, now bar, rest timer, focus header and sheets. `viewport-fit=cover` is already set.
* **Language/time**: dates are formatted server-side in the user's timezone (existing `localtime` filter).

---

## 7. Interaction & JS (`app/static/js/app.js` on every page, `app/static/js/workout.js` on the active workout only; delegated listeners, no inline handlers)

| Hook | Behaviour | No-JS fallback |
|---|---|---|
| `data-set-form` (`workout.js`) | fetch POST with `X-Requested-With: fetch` (CSRF token in the form body); apply the JSON: row classes, `aria-pressed`, `form.action`, rating form action, progress counters, Finish confirm text, `.is-current`; start the rest timer with `rest_seconds` | Full POST + redirect to `#ex-<id>` |
| `data-rate-form` (`workout.js`) | Radio change saves by fetch; the "Save" button is hidden by the `.js` class | Radios + visible Save button |
| `data-rest="seconds"` | Rest timer bar: −15 / +15 / Skip, vibrate at 0 | Timer hidden |
| `data-elapsed-since="ISO"` | Ticks elapsed mm:ss / h:mm:ss every second | Shows start time text |
| `data-stepper` | ± buttons adjust the number input within min/max | Plain number input |
| `data-filter="#list"` | Filters `[data-name]` rows case-insensitively; shows an empty message | Muscle chips are server-side links |
| `data-sheet-open="#id"` | `dialog.showModal()` | `href` goes to a full page |
| `data-confirm`, `data-busy`, `data-copy`, `data-share-url`, `data-use-device-tz` | Existing, unchanged | Existing |
| `.toast` | Auto-dismiss success/info after 4s | Stays in flow |

Use `fetch` only to same origin (`connect-src 'self'`). Never `innerHTML` server JSON. Update text with `textContent` and state with `classList`/`setAttribute`.

---

## 8. CSP-safe dynamic visuals (no `style=""`)

Percentages and widths **must not** use `style="width: 72%"`. Use one of the following:

1. **SVG presentation attributes** (preferred). These are attributes, not inline styles, so `style-src 'self'` allows them.
   * Bar: `<svg class="bar" viewBox="0 0 100 8" preserveAspectRatio="none"><rect class="bar-track" width="100" height="8" rx="4"/><rect class="bar-fill" width="{{ pct }}" height="8" rx="4"/></svg>`
   * Ring: `r=52`, circumference `C = 326.73`. `<circle class="ring-fill" … stroke-dasharray="326.73" stroke-dashoffset="{{ (326.73 * (1 - pct/100))|round(2) }}" transform="rotate(-90 60 60)"/>`
   * Sparkline: `<polyline class="spark-line" points="{{ points }}"/>`, where points are computed in Python from numbers only.
   * Always cast with `|int` / `|float` and clamp to 0–100 in the macro.
2. **Quantised classes** for HTML elements when SVG is awkward: `.w-0`, `.w-5`, … `.w-100` (21 classes in CSS).
3. **JS via CSSOM** (`el.style.setProperty(...)`, which CSP does not block) only for the live rest-timer ring. Prefer `setAttribute('stroke-dashoffset', …)`.

Do not add any new external host. YouTube thumbnails (`i.ytimg.com`) and the nocookie frame are already allowed.

---

## 9. Data the screens need

The binding data model is BUILD_CONTRACT section 1 (new tables only): `TrainerSettings` (unit, default minutes; there is **no** `user_pref`, bodyweight, theme or weekly-goal storage in iteration 1), `ExerciseMuscles`, `EntryPlan` (per-entry targets, rest, progression note, swap options), `SetLog` (one row per completed set; rating `easy`/`ok`/`hard`), `DailyPlan` and `AIUsage`. Templates receive the view objects listed in contract 4.2 (`HeroView`, `RecoveryPanel`, `EntryView`/`SetRowView`, `WorkoutSummary`, `StrengthReport`/`StrengthRow`, `MonthView`/`DayCell`, `HistoryRow`).

Prescriptions follow the AI_COACH §4.5 rule table; the UI shows the result as targets, a progression note and "Next time" deltas.

The AI daily plan prompt receives only: recovery %, last 14 days of trained groups, neglected groups (not trained ≥ 7 days), gym equipment and goal. Output is validated by pydantic (exercise names ≤ 80 chars, sets 1–10, reps 1–30, weight ≥ 0 numeric) and rendered autoescaped.

---

## 10. Rollout order (suggested)

Iteration 1 (done): tokens, typography, icons, 4-tab bar, toasts, Now bar; Today hero (`plan` / `active` / `done` / `rest`), recovery body map, week strip; focus-mode workout with set check-off, ratings, rest timer and swap page; summary; Library and exercise detail; Progress (Strength Index ring, by-muscle bars, top lifts, calendar); Devices; Profile; share pages.

Phase 2: light theme toggle, bodyweight-based standards, index sparkline on Progress, interactive body map, plan-level swaps, `<dialog>` sheets.

## 11. Do / don't

* **Do** keep one volt primary button per view, use tabular numbers, and write labels next to every colour.
* **Do** use real form controls under every custom-looking control.
* **Don't** use emoji as icons, add `style=""`, use inline `<script>`, load webfonts, or copy another app's names, copy, icons or artwork.
* **Don't** show the recovery map, strength data or other days on the public share page.
