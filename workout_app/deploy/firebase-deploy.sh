#!/usr/bin/env bash
# Put FitLog online with Firebase Hosting + Cloud Run + Cloud SQL (Postgres).
#
# Run it in Google Cloud Shell (it already has gcloud and the Firebase CLI), from the repo root:
#     bash workout_app/deploy/firebase-deploy.sh YOUR-FIREBASE-PROJECT-ID
#
# Safe to run again: the first run creates everything; later runs only rebuild and redeploy
# the app (they keep your database, passwords and secrets).
#
# Optional settings (put them before the command, e.g. REGION=us-central1 bash ...):
#   REGION    Cloud Run + database region (default us-east1, South Carolina: closest to Miami)
#   DB_TIER   Cloud SQL machine (default db-f1-micro, the cheapest, roughly $9-12/month)
set -euo pipefail

PROJECT_ID="${1:?Usage: bash workout_app/deploy/firebase-deploy.sh YOUR-FIREBASE-PROJECT-ID}"
REGION="${REGION:-us-east1}"
DB_TIER="${DB_TIER:-db-f1-micro}"
SERVICE="fitlog"
DB_INSTANCE="fitlog-db"
DB_NAME="fitlog"
DB_USER="fitlog"
RUN_SA="fitlog-run"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
secret_exists() { gcloud secrets describe "$1" --project "$PROJECT_ID" >/dev/null 2>&1; }
create_secret() {  # name value  (only if it doesn't exist yet: re-runs keep existing values)
  if ! secret_exists "$1"; then
    printf '%s' "$2" | gcloud secrets create "$1" --project "$PROJECT_ID" \
      --replication-policy=automatic --data-file=- >/dev/null
    echo "  created secret $1"
  fi
}

command -v firebase >/dev/null || { say "Installing the Firebase CLI"; npm install -g firebase-tools; }

say "Using project $PROJECT_ID in $REGION"
gcloud config set project "$PROJECT_ID" >/dev/null

say "Turning on the Google Cloud services FitLog needs (first run takes a minute)"
gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com firebasehosting.googleapis.com

say "Database: Cloud SQL for PostgreSQL ($DB_TIER)"
if ! gcloud sql instances describe "$DB_INSTANCE" >/dev/null 2>&1; then
  echo "  creating $DB_INSTANCE (this takes 5-10 minutes, once)"
  # No authorized networks: only Cloud Run (through the Cloud SQL connector + IAM) can connect.
  gcloud sql instances create "$DB_INSTANCE" \
    --database-version=POSTGRES_16 --edition=ENTERPRISE --tier="$DB_TIER" --region="$REGION" \
    --storage-size=10GB --storage-auto-increase --backup-start-time=08:00
fi
gcloud sql databases describe "$DB_NAME" --instance="$DB_INSTANCE" >/dev/null 2>&1 \
  || gcloud sql databases create "$DB_NAME" --instance="$DB_INSTANCE"
CONNECTION="$(gcloud sql instances describe "$DB_INSTANCE" --format='value(connectionName)')"

if ! secret_exists fitlog-database-url; then
  DB_PASS="$(openssl rand -hex 24)"
  if gcloud sql users list --instance="$DB_INSTANCE" --format='value(name)' | grep -qx "$DB_USER"; then
    gcloud sql users set-password "$DB_USER" --instance="$DB_INSTANCE" --password="$DB_PASS"
  else
    gcloud sql users create "$DB_USER" --instance="$DB_INSTANCE" --password="$DB_PASS"
  fi
  # Cloud Run reaches Cloud SQL through a private unix socket, not the internet.
  create_secret fitlog-database-url \
    "postgresql://${DB_USER}:${DB_PASS}@/${DB_NAME}?host=/cloudsql/${CONNECTION}"
fi

say "Secrets (stored in Secret Manager, never in the code)"
create_secret fitlog-secret-key "$(openssl rand -hex 32)"
create_secret fitlog-token-key "$(openssl rand -hex 32)"

say "A dedicated, least-privilege identity for the app"
SA_EMAIL="${RUN_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$RUN_SA" --display-name="FitLog on Cloud Run"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA_EMAIL" \
  --role=roles/cloudsql.client --condition=None >/dev/null
for s in fitlog-database-url fitlog-secret-key fitlog-token-key \
         fitlog-anthropic-key fitlog-google-client-secret fitlog-oura-client-secret fitlog-youtube-key; do
  secret_exists "$s" && gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$SA_EMAIL" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done

# Optional features: add them with deploy/firebase-set-key.sh, then run this script again.
SECRETS="DATABASE_URL=fitlog-database-url:latest,SECRET_KEY=fitlog-secret-key:latest,TOKEN_ENCRYPTION_KEY=fitlog-token-key:latest"
secret_exists fitlog-anthropic-key && SECRETS+=",ANTHROPIC_API_KEY=fitlog-anthropic-key:latest"
secret_exists fitlog-google-client-secret && SECRETS+=",GOOGLE_CLIENT_SECRET=fitlog-google-client-secret:latest"
secret_exists fitlog-oura-client-secret && SECRETS+=",OURA_CLIENT_SECRET=fitlog-oura-client-secret:latest"
secret_exists fitlog-youtube-key && SECRETS+=",YOUTUBE_API_KEY=fitlog-youtube-key:latest"

PUBLIC_URL="${PUBLIC_URL:-https://${PROJECT_ID}.web.app}"
ENV_VARS="APP_ENV=production,PROXY_FIX_HOPS=2,PUBLIC_URL=${PUBLIC_URL},DEFAULT_TIMEZONE=America/New_York"
[ -n "${GOOGLE_CLIENT_ID:-}" ] && ENV_VARS+=",GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}"
[ -n "${OURA_CLIENT_ID:-}" ] && ENV_VARS+=",OURA_CLIENT_ID=${OURA_CLIENT_ID}"

say "Building and deploying the app to Cloud Run (3-5 minutes)"
gcloud run deploy "$SERVICE" --source "$REPO_ROOT/workout_app" --region "$REGION" \
  --service-account "$SA_EMAIL" --add-cloudsql-instances "$CONNECTION" \
  --set-secrets "$SECRETS" --update-env-vars "$ENV_VARS" \
  --allow-unauthenticated --memory 512Mi --cpu 1 --min-instances 0 --max-instances 3 \
  --timeout 120 --quiet

say "Publishing on Firebase Hosting"
sed -i "s/\"region\": \"[a-z0-9-]*\"/\"region\": \"${REGION}\"/" "$REPO_ROOT/firebase.json"
(cd "$REPO_ROOT" && firebase deploy --only hosting --project "$PROJECT_ID" --non-interactive)

say "Done! Open ${PUBLIC_URL} on your iPhone, create your account, then Share -> Add to Home Screen."
