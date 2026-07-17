#!/usr/bin/env bash
# Upload local Chroma data to the configured GCS bucket.
#
# Usage:
#   ./scripts/gcp/upload-chroma.sh
#   ./scripts/gcp/upload-chroma.sh --dry-run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help)
      echo "Usage: $(basename "$0") [--dry-run]"
      exit 0
      ;;
    *) log_error "Unknown option: $1"; exit 1 ;;
  esac
done

require_cmd gcloud
load_config
gcloud_project

if [[ ! -d "${CHROMA_LOCAL_DIR}" ]] || [[ -z "$(ls -A "${CHROMA_LOCAL_DIR}" 2>/dev/null)" ]]; then
  log_error "Local Chroma directory is missing or empty: ${CHROMA_LOCAL_DIR}"
  log_error "Run video ingestion locally first, or pass --skip-chroma-upload to bootstrap.sh"
  exit 1
fi

if ! bucket_exists; then
  log_error "Bucket gs://${BUCKET} does not exist. Run ./scripts/gcp/bootstrap.sh first."
  exit 1
fi

log_info "Uploading ${CHROMA_LOCAL_DIR} -> gs://${BUCKET}/"

if [[ "${DRY_RUN}" == "true" ]]; then
  gcloud storage ls -r "gs://${BUCKET}/" | head -20 || true
  du -sh "${CHROMA_LOCAL_DIR}"
  log_info "Dry run only; no files copied."
  exit 0
fi

gcloud storage cp -r "${CHROMA_LOCAL_DIR}/"* "gs://${BUCKET}/"
log_info "Chroma upload complete."
