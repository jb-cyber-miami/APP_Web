#!/usr/bin/env bash
# Store or replace an optional API key in Secret Manager, then redeploy to apply it.
#
#   bash workout_app/deploy/firebase-set-key.sh YOUR-PROJECT-ID anthropic
#   bash workout_app/deploy/firebase-set-key.sh YOUR-PROJECT-ID google-client-secret
#   bash workout_app/deploy/firebase-set-key.sh YOUR-PROJECT-ID oura-client-secret
#   bash workout_app/deploy/firebase-set-key.sh YOUR-PROJECT-ID youtube
#
# The key is typed in hidden (not echoed, not saved in your shell history).
# The Google/Oura *client IDs* are not secret: pass them when redeploying instead, e.g.
#   GOOGLE_CLIENT_ID=123.apps.googleusercontent.com bash workout_app/deploy/firebase-deploy.sh ID
set -euo pipefail

PROJECT_ID="${1:?Usage: firebase-set-key.sh PROJECT_ID anthropic|google-client-secret|oura-client-secret|youtube}"
case "${2:-}" in
  anthropic) NAME=fitlog-anthropic-key ;;
  google-client-secret) NAME=fitlog-google-client-secret ;;
  oura-client-secret) NAME=fitlog-oura-client-secret ;;
  youtube) NAME=fitlog-youtube-key ;;
  *) echo "Second argument must be: anthropic, google-client-secret, oura-client-secret or youtube" >&2; exit 1 ;;
esac

read -r -s -p "Paste the value for $NAME and press Enter: " VALUE; echo
[ -n "$VALUE" ] || { echo "Nothing entered; nothing changed." >&2; exit 1; }

if gcloud secrets describe "$NAME" --project "$PROJECT_ID" >/dev/null 2>&1; then
  printf '%s' "$VALUE" | gcloud secrets versions add "$NAME" --project "$PROJECT_ID" --data-file=- >/dev/null
else
  printf '%s' "$VALUE" | gcloud secrets create "$NAME" --project "$PROJECT_ID" \
    --replication-policy=automatic --data-file=- >/dev/null
fi
echo "Saved $NAME. Now run: bash workout_app/deploy/firebase-deploy.sh $PROJECT_ID"
