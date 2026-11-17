#!/usr/bin/env bash
# Deploy Aero to Google Cloud Run (free tier), with the SQLite cache persisted in
# Cloud Storage. Safe to re-run: existing resources are reused, and each run
# deploys the current working tree.
#
# Usage:  PROJECT_ID=my-project deploy/cloud-run.sh
# Optional env: REGION (us-central1), SERVICE (aero), BUCKET (<project>-aero-cache)
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project id}"
REGION="${REGION:-us-central1}"   # free-tier region; Cloud Storage's free 5 GB is US-only too
SERVICE="${SERVICE:-aero}"
BUCKET="${BUCKET:-${PROJECT_ID}-aero-cache}"
SA_NAME="${SERVICE}-run"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
ADMIN_SECRET="${SERVICE}-admin-password"
SERPAPI_SECRET="${SERVICE}-serpapi-key"

cd "$(dirname "$0")/.."
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }

gcloud config set project "$PROJECT_ID" >/dev/null

step "Enabling APIs (first run takes a minute)"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com storage.googleapis.com

step "Service account $SA"
exists gcloud iam service-accounts describe "$SA" ||
  gcloud iam service-accounts create "$SA_NAME" --display-name "Aero (Cloud Run)"

step "Cache bucket gs://$BUCKET"
if ! exists gcloud storage buckets describe "gs://$BUCKET"; then
  gcloud storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access
fi
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$SA" --role roles/storage.objectAdmin >/dev/null

step "Secrets"
create_secret() {  # name, prompt, required(1/0)
  if exists gcloud secrets describe "$1"; then echo "  $1: exists (update with: gcloud secrets versions add $1 --data-file=-)"; return 0; fi
  local value; read -rsp "  $2: " value; echo
  if [[ -z "$value" ]]; then
    [[ "$3" == 1 ]] && { echo "  $1 is required" >&2; exit 1; }
    echo "  skipped $1"; return 1
  fi
  printf '%s' "$value" | gcloud secrets create "$1" --data-file=- --replication-policy automatic >/dev/null
}
grant_secret() {
  gcloud secrets add-iam-policy-binding "$1" --member "serviceAccount:$SA" \
    --role roles/secretmanager.secretAccessor >/dev/null
}
create_secret "$ADMIN_SECRET" "Admin password (needed to change settings on the site)" 1
grant_secret "$ADMIN_SECRET"
SECRETS="ADMIN_PASSWORD=${ADMIN_SECRET}:latest"
if create_secret "$SERPAPI_SECRET" "SerpApi key (optional, Enter to skip)" 0 || exists gcloud secrets describe "$SERPAPI_SECRET"; then
  grant_secret "$SERPAPI_SECRET"
  SECRETS="${SECRETS},SERPAPI_API_KEY=${SERPAPI_SECRET}:latest"
fi

step "Building and deploying $SERVICE to $REGION"
# max-instances=1: the fetch queue and cache live in one process.
# timeout=3600: lets the live-update stream stay open (browsers reconnect after).
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$SA" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 1 \
  --cpu 1 --memory 512Mi \
  --timeout 3600 \
  --set-env-vars "PROVIDER_MODE=fast_flights,CACHE_TTL_DAYS=2,CACHE_SNAPSHOT_URI=gs://${BUCKET}/flights.db" \
  --set-secrets "$SECRETS"

step "Done"
gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)'
