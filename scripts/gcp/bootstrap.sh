#!/usr/bin/env bash
# Phase 2: one-time (or idempotent re-run) GCP infrastructure bootstrap.
#
# Usage:
#   ./scripts/gcp/bootstrap.sh
#   ./scripts/gcp/bootstrap.sh --update-secrets
#   ./scripts/gcp/bootstrap.sh --skip-chroma-upload
#
# Prerequisites:
#   - gcloud installed and authenticated (gcloud auth login)
#   - scripts/gcp/config.env copied from config.env.example
#   - Local API key files at project root (for initial secret creation)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

UPDATE_SECRETS=false
SKIP_CHROMA_UPLOAD=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --update-secrets       Add new Secret Manager versions from local key files
  --skip-chroma-upload   Do not upload local Chroma data to GCS
  -h, --help             Show this help

Idempotent: safe to re-run. Creates missing resources; skips existing ones.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --update-secrets) UPDATE_SECRETS=true; shift ;;
    --skip-chroma-upload) SKIP_CHROMA_UPLOAD=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) log_error "Unknown option: $1"; usage; exit 1 ;;
  esac
done

require_cmd gcloud
load_config
gcloud_project

log_info "Bootstrap starting for project=${PROJECT_ID} region=${REGION}"

# --- APIs (idempotent) ---
log_info "Enabling required APIs"
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  --project="${PROJECT_ID}"

# --- Artifact Registry ---
if artifact_repo_exists; then
  log_info "Artifact Registry repo '${REPO}' already exists"
else
  log_info "Creating Artifact Registry repo '${REPO}'"
  gcloud artifacts repositories create "${REPO}" \
    --project="${PROJECT_ID}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="21days media search API images"
fi

log_info "Configuring Docker auth for ${REGION}-docker.pkg.dev"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# --- GCS bucket ---
if bucket_exists; then
  log_info "GCS bucket gs://${BUCKET} already exists"
else
  log_info "Creating GCS bucket gs://${BUCKET}"
  gcloud storage buckets create "gs://${BUCKET}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --uniform-bucket-level-access
fi

# --- Runtime service account ---
SA_JUST_CREATED=false
if service_account_exists; then
  log_info "Service account ${RUNTIME_SA} already exists"
else
  log_info "Creating service account ${RUNTIME_SA_NAME}"
  gcloud iam service-accounts create "${RUNTIME_SA_NAME}" \
    --project="${PROJECT_ID}" \
    --display-name="na21days-media Cloud Run runtime"
  SA_JUST_CREATED=true
fi

if [[ "${SA_JUST_CREATED}" == "true" ]]; then
  wait_for_service_account
fi

# --- IAM bindings (additive; safe to re-run; retries for IAM propagation) ---
log_info "Granting Secret Manager access to runtime SA"
grant_project_role_with_retry "roles/secretmanager.secretAccessor"

log_info "Granting GCS object access on gs://${BUCKET}"
grant_bucket_role_with_retry "roles/storage.objectUser"

# --- Secrets ---
ensure_secret_from_file "${SECRET_OPENAI}" "${OPENAI_KEY_PATH}" "${UPDATE_SECRETS}"
ensure_secret_from_file "${SECRET_YOUTUBE}" "${YOUTUBE_KEY_PATH}" "${UPDATE_SECRETS}"
ensure_admin_secret "${UPDATE_SECRETS}"

# --- Chroma upload (Phase 3 bundled for convenience) ---
if [[ "${SKIP_CHROMA_UPLOAD}" == "true" ]]; then
  log_info "Skipping Chroma upload (--skip-chroma-upload)"
else
  "${SCRIPT_DIR}/upload-chroma.sh"
fi

log_info "Bootstrap complete."
log_info "Next: ./scripts/gcp/verify.sh"
log_info "Then: ./scripts/gcp/deploy.sh"
