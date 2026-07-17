#!/usr/bin/env bash
# Shared helpers for scripts/gcp/*.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.env"

log_info()  { printf '\033[0;32m[INFO]\033[0m %s\n' "$*"; }
log_warn()  { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
log_error() { printf '\033[0;31m[ERROR]\033[0m %s\n' "$*" >&2; }

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    log_error "Required command not found: ${cmd}"
    exit 1
  fi
}

load_config() {
  if [[ ! -f "${CONFIG_FILE}" ]]; then
    log_error "Missing ${CONFIG_FILE}"
    log_error "Copy scripts/gcp/config.env.example to scripts/gcp/config.env and edit it."
    exit 1
  fi
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"

  : "${PROJECT_ID:?PROJECT_ID required in config.env}"
  : "${REGION:?REGION required in config.env}"
  : "${REPO:?REPO required in config.env}"
  : "${SERVICE:?SERVICE required in config.env}"
  : "${BUCKET:?BUCKET required in config.env}"
  : "${RUNTIME_SA_NAME:?RUNTIME_SA_NAME required in config.env}"

  RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}"
  CHROMA_LOCAL_DIR="${PROJECT_ROOT}/resources/chroma_free_store"
  OPENAI_KEY_PATH="${PROJECT_ROOT}/${OPENAI_KEY_FILE:-openai_api_key.txt}"
  YOUTUBE_KEY_PATH="${PROJECT_ROOT}/${YOUTUBE_KEY_FILE:-api_key.txt}"
  CHROMA_MOUNT_PATH="${CHROMA_MOUNT_PATH:-/mnt/chroma_data}"
  SECRET_OPENAI="${SECRET_OPENAI:-openai-api-key}"
  SECRET_YOUTUBE="${SECRET_YOUTUBE:-youtube-api-key}"
  SECRET_ADMIN="${SECRET_ADMIN:-admin-api-key}"
}

gcloud_project() {
  gcloud config set project "${PROJECT_ID}" >/dev/null
}

secret_exists() {
  gcloud secrets describe "$1" --project="${PROJECT_ID}" >/dev/null 2>&1
}

artifact_repo_exists() {
  gcloud artifacts repositories describe "${REPO}" \
    --location="${REGION}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1
}

bucket_exists() {
  gcloud storage buckets describe "gs://${BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1
}

service_account_exists() {
  gcloud iam service-accounts describe "${RUNTIME_SA}" --project="${PROJECT_ID}" >/dev/null 2>&1
}

# Newly created service accounts can take several seconds to be usable in IAM bindings.
wait_for_service_account() {
  local attempts="${1:-12}"
  local sleep_secs="${2:-5}"
  local i

  log_info "Waiting for service account ${RUNTIME_SA} to become usable in IAM..."
  for ((i = 1; i <= attempts; i++)); do
    if service_account_exists; then
      # Extra settle time for IAM propagation after describe succeeds
      sleep 2
      log_info "Service account is ready (attempt ${i}/${attempts})"
      return 0
    fi
    log_warn "Service account not ready yet; retrying in ${sleep_secs}s (${i}/${attempts})"
    sleep "${sleep_secs}"
  done

  log_error "Timed out waiting for service account ${RUNTIME_SA}"
  return 1
}

grant_project_role_with_retry() {
  local role="$1"
  local attempts="${2:-8}"
  local sleep_secs="${3:-5}"
  local i
  local err_file
  err_file="$(mktemp)"

  for ((i = 1; i <= attempts; i++)); do
    if gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${RUNTIME_SA}" \
      --role="${role}" \
      --quiet >/dev/null 2>"${err_file}"; then
      rm -f "${err_file}"
      return 0
    fi
    if grep -qi "does not exist\|not found\|INVALID_ARGUMENT" "${err_file}"; then
      log_warn "IAM bind for ${role} not ready yet; retrying in ${sleep_secs}s (${i}/${attempts})"
      sleep "${sleep_secs}"
      continue
    fi
    cat "${err_file}" >&2
    rm -f "${err_file}"
    return 1
  done

  cat "${err_file}" >&2
  rm -f "${err_file}"
  log_error "Failed to grant ${role} to ${RUNTIME_SA} after ${attempts} attempts"
  return 1
}

grant_bucket_role_with_retry() {
  local role="$1"
  local attempts="${2:-8}"
  local sleep_secs="${3:-5}"
  local i
  local err_file
  err_file="$(mktemp)"

  for ((i = 1; i <= attempts; i++)); do
    if gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
      --member="serviceAccount:${RUNTIME_SA}" \
      --role="${role}" \
      --quiet >/dev/null 2>"${err_file}"; then
      rm -f "${err_file}"
      return 0
    fi
    if grep -qi "does not exist\|not found\|INVALID_ARGUMENT\|Permission denied" "${err_file}"; then
      log_warn "Bucket IAM bind for ${role} not ready yet; retrying in ${sleep_secs}s (${i}/${attempts})"
      sleep "${sleep_secs}"
      continue
    fi
    cat "${err_file}" >&2
    rm -f "${err_file}"
    return 1
  done

  cat "${err_file}" >&2
  rm -f "${err_file}"
  log_error "Failed to grant ${role} on gs://${BUCKET} after ${attempts} attempts"
  return 1
}

ensure_secret_from_file() {
  local secret_name="$1"
  local file_path="$2"
  local update_secrets="${3:-false}"

  if [[ ! -f "${file_path}" ]]; then
    log_warn "Skipping secret ${secret_name}: file not found (${file_path})"
    return 0
  fi

  if secret_exists "${secret_name}"; then
    if [[ "${update_secrets}" == "true" ]]; then
      log_info "Updating secret ${secret_name} (new version)"
      gcloud secrets versions add "${secret_name}" \
        --project="${PROJECT_ID}" \
        --data-file="${file_path}"
    else
      log_info "Secret ${secret_name} already exists (use --update-secrets to rotate)"
    fi
  else
    log_info "Creating secret ${secret_name}"
    gcloud secrets create "${secret_name}" \
      --project="${PROJECT_ID}" \
      --replication-policy=automatic \
      --data-file="${file_path}"
  fi
}

ensure_admin_secret() {
  local update_secrets="${1:-false}"

  if secret_exists "${SECRET_ADMIN}"; then
    if [[ "${update_secrets}" == "true" ]]; then
      log_warn "Rotating ${SECRET_ADMIN} with a newly generated value"
      openssl rand -hex 32 | gcloud secrets versions add "${SECRET_ADMIN}" \
        --project="${PROJECT_ID}" \
        --data-file=-
      log_warn "Save the new admin key:"
      gcloud secrets versions access latest --secret="${SECRET_ADMIN}" --project="${PROJECT_ID}"
    else
      log_info "Secret ${SECRET_ADMIN} already exists (use --update-secrets to rotate)"
    fi
    return 0
  fi

  log_info "Creating secret ${SECRET_ADMIN}"
  openssl rand -hex 32 | gcloud secrets create "${SECRET_ADMIN}" \
    --project="${PROJECT_ID}" \
    --replication-policy=automatic \
    --data-file=-
  log_warn "Save this admin key for X-Admin-Key HTTP calls:"
  gcloud secrets versions access latest --secret="${SECRET_ADMIN}" --project="${PROJECT_ID}"
}

default_image_tag() {
  if command -v git >/dev/null 2>&1 && git -C "${PROJECT_ROOT}" rev-parse --short HEAD >/dev/null 2>&1; then
    git -C "${PROJECT_ROOT}" rev-parse --short HEAD
  else
    date +%Y%m%d-%H%M%S
  fi
}
