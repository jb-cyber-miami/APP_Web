# Put FitLog online with Google Firebase

```
 iPhone ──HTTPS──► Firebase Hosting  (https://PROJECT-ID.web.app, CDN, free SSL)
                        │  forwards every request
                        ▼
                   Cloud Run "fitlog"  (the Python app in a container, scales to zero)
                        │  private socket          │  reads keys at start-up
                        ▼                          ▼
              Cloud SQL PostgreSQL          Secret Manager
              "fitlog-db" (your data,       (database password, app keys,
               daily backups)                optional API keys)
```

Firebase Hosting can't run Python by itself, so it forwards requests to Cloud Run. All of
these are in the **same Google project**, billed together, and managed from the Firebase and
Google Cloud consoles.

## One-time setup (about 20 minutes, mostly waiting)

1. **Create the Firebase project.** Go to <https://console.firebase.google.com> → **Create a
   project**. Pick a name such as `fitlog-jones`. Google Analytics isn't needed. Write down
   the **Project ID** shown under the name (for example `fitlog-jones` or `fitlog-jones-1a2b3`).
2. **Switch to the Blaze plan.** In the Firebase console, open **Upgrade** (bottom left) →
   **Blaze (pay as you go)** and add a card. Cloud Run and Cloud SQL require it.
3. **Set a budget alert** so there are no surprises: <https://console.cloud.google.com/billing>
   → **Budgets & alerts** → **Create budget**, for example $20/month with email alerts.
4. **Open Cloud Shell**, a free terminal in your browser with all the tools installed:
   <https://shell.cloud.google.com> (use the same Google account).
5. **Sign the Firebase tool in** (only once; follow the link it prints):
   ```bash
   firebase login --no-localhost
   ```
6. **Get the code and run the setup script:**
   ```bash
   git clone https://github.com/jb-cyber-miami/APP_Web.git
   cd APP_Web
   bash workout_app/deploy/firebase-deploy.sh YOUR-PROJECT-ID
   ```
   If the repository is private, `git clone` asks for your GitHub username and a
   [personal access token](https://github.com/settings/tokens) as the password.
   If the code isn't merged into `main` yet, run `git checkout claude/workout-routine-app-3dwtms`
   before the script.
7. When it says **Done!**, open `https://YOUR-PROJECT-ID.web.app` on your iPhone, create your
   account, then Safari → Share → **Add to Home Screen**.

The script turns on the Google services, creates the database (5-10 minutes the first time),
generates passwords and keys into Secret Manager, creates a locked-down service account for
the app, builds and deploys the container, and publishes Firebase Hosting.

## Turning on the optional features

Each key is typed in hidden and stored in Secret Manager. Re-run the deploy script afterwards.

| Feature | Command | Also needed |
|---|---|---|
| AI coach (Claude) | `bash workout_app/deploy/firebase-set-key.sh ID anthropic` | Key from <https://console.anthropic.com> (set a spend limit there) |
| Google sign-in | `bash workout_app/deploy/firebase-set-key.sh ID google-client-secret` | OAuth client in Google Cloud → APIs & Services → Credentials, redirect URI `https://ID.web.app/auth/google/callback`; deploy with `GOOGLE_CLIENT_ID=... bash workout_app/deploy/firebase-deploy.sh ID` |
| Oura Ring | `bash workout_app/deploy/firebase-set-key.sh ID oura-client-secret` | App at <https://cloud.ouraring.com/oauth/applications>, redirect URI `https://ID.web.app/integrations/oura/callback`; deploy with `OURA_CLIENT_ID=... bash workout_app/deploy/firebase-deploy.sh ID` |
| Auto YouTube videos | `bash workout_app/deploy/firebase-set-key.sh ID youtube` | YouTube Data API v3 key |

Apple Watch: in the app go to **Profile → Devices**, create a token, and use the addresses it
shows (`https://ID.web.app/api/v1/apple/...`) in the iPhone Shortcuts (docs/INTEGRATIONS.md).

When you deploy with Google or Oura turned on, pass **every** client ID you use in the same
command, e.g. `GOOGLE_CLIENT_ID=... OURA_CLIENT_ID=... bash workout_app/deploy/firebase-deploy.sh ID`.
A client ID you leave out keeps its previous value.

## Updating the app later

In Cloud Shell:
```bash
cd APP_Web && git pull
bash workout_app/deploy/firebase-deploy.sh YOUR-PROJECT-ID
```
Your data, passwords and keys are kept; only the app is rebuilt.

## What it costs (approximate, US regions)

| Piece | Cost |
|---|---|
| Cloud SQL PostgreSQL, `db-f1-micro`, 10 GB, daily backups | about $10-14 / month (the main cost) |
| Cloud Run | $0 for personal use (free tier: 2 million requests / month) |
| Firebase Hosting | $0 (free tier) |
| Secret Manager, Artifact Registry (container storage) | cents |
| Claude (only if you add the key) | about $0.05-0.09 per AI-built workout, capped at 25 calls/user/day |

To pause the database bill you can stop the instance in Google Cloud → SQL (the app is down
while it's stopped). Deleting the Google project removes everything and stops all charges.

## Good to know

* **First visit after a quiet period takes a few seconds**: Cloud Run starts the app on demand
  and shuts it down when idle (that's what keeps it free). To keep one copy warm, add
  `--min-instances 1` in the script (costs a few dollars a month).
* **Sign-in cookie:** Firebase Hosting only passes one cookie, named `__session`, to the app.
  FitLog uses exactly that name. "Keep me signed in" isn't needed: sessions last 30 days.
* **Time limit:** Firebase Hosting gives the app about 60 seconds per request. An AI-built
  workout usually takes 20-40 seconds. If it ever takes longer, the page shows an error but
  the plan is still saved: reload Today.
* **Region:** everything runs in `us-east1` (South Carolina, closest to Miami). To use another
  region, run the first setup with e.g. `REGION=us-central1 bash ...` (keep it the same on every
  run; the script also updates `firebase.json`).
* **Backups:** Cloud SQL backs up the database daily at 08:00 UTC and keeps 7 days. Restore
  from Google Cloud → SQL → fitlog-db → Backups.
* **Security:** the database has no public access list (only the app's service account can
  connect, through Google's connector); keys live in Secret Manager; the app runs as a
  non-root user; HTTPS everywhere. See SECURITY.md.

## Troubleshooting

| Message | Fix |
|---|---|
| `firebase: ... not authorized` / `Failed to get Firebase project` | Run `firebase login --no-localhost` and check the project ID; the project must be created in the **Firebase** console (step 1). |
| `Billing account ... not found` / `requires billing` | Finish step 2 (Blaze plan). |
| Cloud Build `PERMISSION_DENIED` while deploying | Google Cloud → IAM: give the *Compute Engine default service account* the **Cloud Build Service Account** role, then re-run. |
| `region ... is not supported` from Firebase | Re-run the whole setup with `REGION=us-central1`. |
| Page shows "Service Unavailable" | Google Cloud → Cloud Run → fitlog → **Logs** shows why (usually a missing secret or the database still starting). |

## Checking it after the first deploy

* Sign in from your phone on Wi-Fi, then on cellular: you should stay signed in.
* `PROXY_FIX_HOPS=2` tells the app that two Google proxies sit in front of it, so it can see
  your real IP address for the login rate limits. If Cloud Run's logs show login attempts all
  coming from the same Google IP address, change it with
  `gcloud run services update fitlog --region us-east1 --update-env-vars PROXY_FIX_HOPS=1`.
