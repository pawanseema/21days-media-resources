#!/usr/bin/env bash
# Phase 4: build container image and deploy to Cloud Run.
#
# Usage:
#   ./scripts/gcp/deploy.sh
#   ./scripts/gcp/deploy.sh --tag v1.0.0
#   ./scripts/gcp/deploy.sh --no-build    # redeploy existing image tag

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

IMAGE_TAG=""
NO_BUILD=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --tag TAG       Image tag (default: git short SHA or timestamp)
  --no-build      Skip gcloud builds submit; redeploy existing tag only
  -h, --help      Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) IMAGE_TAG="$2"; shift 2 ;;
    --no-build) NO_BUILD=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) log_error "Unknown option: $1"; usage; exit 1 ;;
  esac
done

require_cmd gcloud
load_config
gcloud_project

if [[ -z "${IMAGE_TAG}" ]]; then
  IMAGE_TAG="$(default_image_tag)"
fi

IMAGE="${IMAGE_BASE}:${IMAGE_TAG}"
RUN_MEMORY="${RUN_MEMORY:-1Gi}"
RUN_TIMEOUT="${RUN_TIMEOUT:-300}"
# Single instance: Chroma SQLite on GCS FUSE is safer with one container.
# Concurrent requests still work via gunicorn threads (see gunicorn.conf.py).
RUN_MAX_INSTANCES="${RUN_MAX_INSTANCES:-1}"
RUN_MIN_INSTANCES="${RUN_MIN_INSTANCES:-0}"

if ! artifact_repo_exists; then
  log_error "Artifact Registry repo missing. Run ./scripts/gcp/bootstrap.sh first."
  exit 1
fi

if ! bucket_exists; then
  log_error "GCS bucket missing. Run ./scripts/gcp/bootstrap.sh first."
  exit 1
fi

for secret in "${SECRET_OPENAI}" "${SECRET_YOUTUBE}" "${SECRET_ADMIN}"; do
  if ! secret_exists "${secret}"; then
    log_error "Secret missing: ${secret}. Run ./scripts/gcp/bootstrap.sh first."
    exit 1
  fi
done

if [[ "${NO_BUILD}" == "false" ]]; then
  log_info "Building and pushing image: ${IMAGE}"
  gcloud builds submit "${PROJECT_ROOT}" --tag "${IMAGE}" --project="${PROJECT_ID}"
else
  log_info "Skipping build; deploying existing image: ${IMAGE}"
fi

log_info "Deploying Cloud Run service: ${SERVICE}"
gcloud beta run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${RUNTIME_SA}" \
  --platform=managed \
  --execution-environment=gen2 \
  --min-instances="${RUN_MIN_INSTANCES}" \
  --max-instances="${RUN_MAX_INSTANCES}" \
  --memory="${RUN_MEMORY}" \
  --timeout="${RUN_TIMEOUT}" \
  --allow-unauthenticated \
  --set-env-vars="CHROMA_PERSIST_DIR=${CHROMA_MOUNT_PATH},SHOW_RESULT_DEBUG=false,ENABLE_MORE_LIKE_THIS=true" \
  --set-secrets="OPENAI_API_KEY=${SECRET_OPENAI}:latest,YOUTUBE_API_KEY=${SECRET_YOUTUBE}:latest,ADMIN_API_KEY=${SECRET_ADMIN}:latest" \
  --add-volume="mount-path=${CHROMA_MOUNT_PATH},type=cloud-storage,bucket=${BUCKET},readonly=false"

SERVICE_URL="$(gcloud run services describe "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(status.url)')"

log_info "Deploy complete."
log_info "Service URL: ${SERVICE_URL}"
log_info "Health:  curl ${SERVICE_URL}/health"
log_info "Search:  curl -X POST ${SERVICE_URL}/search -H 'Content-Type: application/json' -d '{\"query\":\"meditation\",\"top_k\":2}'"
