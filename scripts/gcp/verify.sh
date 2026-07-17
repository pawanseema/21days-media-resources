#!/usr/bin/env bash
# Verify GCP infrastructure and (optionally) a deployed Cloud Run service.
#
# Usage:
#   ./scripts/gcp/verify.sh
#   ./scripts/gcp/verify.sh --smoke   # also curl /health on Cloud Run URL

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

SMOKE=false
FAILURES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --smoke) SMOKE=true; shift ;;
    -h|--help)
      echo "Usage: $(basename "$0") [--smoke]"
      exit 0
      ;;
    *) log_error "Unknown option: $1"; exit 1 ;;
  esac
done

check() {
  local label="$1"
  shift
  if "$@"; then
    log_info "OK  ${label}"
  else
    log_error "FAIL ${label}"
    FAILURES=$((FAILURES + 1))
  fi
}

require_cmd gcloud
load_config
gcloud_project

log_info "Verifying GCP resources for ${PROJECT_ID}"

check "billing enabled" bash -c "[[ \"\$(gcloud billing projects describe '${PROJECT_ID}' --format='value(billingEnabled)')\" == 'True' ]]"
check "Artifact Registry repo ${REPO}" artifact_repo_exists
check "GCS bucket gs://${BUCKET}" bucket_exists
check "runtime service account ${RUNTIME_SA}" service_account_exists
check "secret ${SECRET_OPENAI}" secret_exists "${SECRET_OPENAI}"
check "secret ${SECRET_YOUTUBE}" secret_exists "${SECRET_YOUTUBE}"
check "secret ${SECRET_ADMIN}" secret_exists "${SECRET_ADMIN}"

OBJECT_COUNT="$(gcloud storage ls "gs://${BUCKET}/" 2>/dev/null | wc -l | tr -d ' ')"
if [[ "${OBJECT_COUNT}" -gt 0 ]]; then
  log_info "OK  Chroma objects in bucket (top-level entries: ${OBJECT_COUNT})"
else
  log_warn "WARN Chroma bucket appears empty; run ./scripts/gcp/upload-chroma.sh"
fi

if gcloud run services describe "${SERVICE}" --region="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  SERVICE_URL="$(gcloud run services describe "${SERVICE}" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --format='value(status.url)')"
  log_info "OK  Cloud Run service ${SERVICE} deployed"
  log_info "    URL: ${SERVICE_URL}"

  if [[ "${SMOKE}" == "true" ]]; then
    require_cmd curl
    if curl -sf "${SERVICE_URL}/health" >/dev/null; then
      log_info "OK  GET /health"
    else
      log_error "FAIL GET /health"
      FAILURES=$((FAILURES + 1))
    fi
  fi
else
  log_warn "Cloud Run service ${SERVICE} not deployed yet (run ./scripts/gcp/deploy.sh)"
fi

if [[ "${FAILURES}" -gt 0 ]]; then
  log_error "Verification finished with ${FAILURES} failure(s)"
  exit 1
fi

log_info "Verification passed."
