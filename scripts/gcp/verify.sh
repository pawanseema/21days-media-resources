#!/usr/bin/env bash
# Verify GCP infrastructure and (optionally) a deployed Cloud Run service.
#
# Usage:
#   ./scripts/gcp/verify.sh
#   ./scripts/gcp/verify.sh --smoke   # curl /health, /api/ui-config, /api/videos/related

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

SMOKE=false
FAILURES=0

# Sample seed used by local Chroma / smoke scripts (Day 21 intro segment).
RELATED_SEED_JSON='{"video_id":"CO1vvOCoLjI","timestamp":"4:04","top_k":3}'

while [[ $# -gt 0 ]]; do
  case "$1" in
    --smoke) SMOKE=true; shift ;;
    -h|--help)
      echo "Usage: $(basename "$0") [--smoke]"
      echo "  --smoke  Hit Cloud Run /health, /api/ui-config, and /api/videos/related"
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

smoke_fail() {
  log_error "FAIL $1"
  FAILURES=$((FAILURES + 1))
}

run_http_smoke() {
  local base_url="$1"
  require_cmd curl
  require_cmd python3

  local tmp_dir
  tmp_dir="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '${tmp_dir}'" RETURN

  # --- /health ---
  if curl -sf "${base_url}/health" >/dev/null; then
    log_info "OK  GET /health"
  else
    smoke_fail "GET /health"
  fi

  # --- /api/ui-config ---
  # showResultDebug is required. enableMoreLikeThis is required only after the
  # more-like-this revision is deployed; older revisions omit it (treat as off).
  local ui_config_file="${tmp_dir}/ui-config.json"
  local ui_http
  ui_http="$(curl -sS -o "${ui_config_file}" -w '%{http_code}' "${base_url}/api/ui-config" || true)"
  if [[ "${ui_http}" != "200" ]]; then
    smoke_fail "GET /api/ui-config (HTTP ${ui_http})"
  else
    local ui_status
    ui_status="$(python3 - "${ui_config_file}" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
if not isinstance(data.get("showResultDebug"), bool):
    print("fail")
    print("showResultDebug must be a boolean", file=sys.stderr)
    sys.exit(0)
mlt = data.get("enableMoreLikeThis", None)
if mlt is None:
    print("legacy")
    print(
        f"showResultDebug={data['showResultDebug']} "
        "(enableMoreLikeThis absent — pre–more-like-this revision; treat as off)",
        file=sys.stderr,
    )
elif not isinstance(mlt, bool):
    print("fail")
    print("enableMoreLikeThis must be a boolean when present", file=sys.stderr)
else:
    print("ok")
    print(
        f"enableMoreLikeThis={mlt} showResultDebug={data['showResultDebug']}",
        file=sys.stderr,
    )
PY
)" || ui_status="fail"
    case "${ui_status}" in
      ok)
        log_info "OK  GET /api/ui-config (enableMoreLikeThis + showResultDebug present)"
        ;;
      legacy)
        log_warn "WARN GET /api/ui-config: enableMoreLikeThis missing — deploy cursor/more-like-this to Cloud Run to enable the feature and full smoke checks"
        log_info "OK  GET /api/ui-config (showResultDebug present; more-like-this not on this revision)"
        ;;
      *)
        smoke_fail "GET /api/ui-config invalid flags"
        cat "${ui_config_file}" >&2 || true
        ;;
    esac
  fi

  local enable_mlt="false"
  if [[ -f "${ui_config_file}" ]]; then
    enable_mlt="$(python3 -c "import json; print(json.load(open('${ui_config_file}')).get('enableMoreLikeThis', False))" 2>/dev/null || echo false)"
  fi

  # --- tab config endpoints (must be in the container image under config/) ---
  local wisdom_http
  wisdom_http="$(curl -sS -o "${tmp_dir}/wisdom.json" -w '%{http_code}' "${base_url}/api/wisdom/topics" || true)"
  if [[ "${wisdom_http}" == "200" ]]; then
    if python3 - "${tmp_dir}/wisdom.json" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
topics = data.get("topics")
if not isinstance(topics, list) or not topics:
    raise SystemExit(1)
PY
    then
      log_info "OK  GET /api/wisdom/topics"
    else
      smoke_fail "GET /api/wisdom/topics invalid payload"
    fi
  else
    smoke_fail "GET /api/wisdom/topics (HTTP ${wisdom_http}; config/wisdom_topics.json missing from image?)"
  fi

  for tab_path in /api/live/sessions /api/recordings; do
    local tab_http
    tab_http="$(curl -sS -o /dev/null -w '%{http_code}' "${base_url}${tab_path}" || true)"
    if [[ "${tab_http}" == "200" || "${tab_http}" == "503" ]]; then
      log_info "OK  GET ${tab_path} (HTTP ${tab_http})"
    else
      smoke_fail "GET ${tab_path} (HTTP ${tab_http}; config/*.json missing from image or fatal YouTube error)"
    fi
  done

  # --- /api/videos/related (behavior depends on ENABLE_MORE_LIKE_THIS) ---
  local related_file="${tmp_dir}/related.json"
  local related_http
  related_http="$(curl -sS -o "${related_file}" -w '%{http_code}' \
    -X POST "${base_url}/api/videos/related" \
    -H 'Content-Type: application/json' \
    -d "${RELATED_SEED_JSON}" || true)"

  if [[ "${enable_mlt}" == "True" || "${enable_mlt}" == "true" ]]; then
    if [[ "${related_http}" != "200" ]]; then
      smoke_fail "POST /api/videos/related expected 200 when flag on (HTTP ${related_http})"
      cat "${related_file}" >&2 || true
    else
      local related_ok
      related_ok="$(python3 - "${related_file}" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
results = data.get("results") or []
seed = data.get("seed") or {}
seed_vid = seed.get("video_id") or "CO1vvOCoLjI"
ok = isinstance(results, list) and len(results) > 0
same = [r for r in results if r.get("video_id") == seed_vid]
print("true" if ok and not same else "false")
if not ok:
    print("related response missing non-empty results", file=sys.stderr)
elif same:
    print(f"related results include same video_id={seed_vid}", file=sys.stderr)
else:
    print(f"count={len(results)} seed={seed_vid}", file=sys.stderr)
PY
)" || related_ok="false"
      if [[ "${related_ok}" == "true" ]]; then
        log_info "OK  POST /api/videos/related (flag on; neighbors exclude seed video)"
      else
        smoke_fail "POST /api/videos/related payload invalid when flag on"
        cat "${related_file}" >&2 || true
      fi
    fi
  else
    if [[ "${related_http}" != "404" ]]; then
      smoke_fail "POST /api/videos/related expected 404 when flag off (HTTP ${related_http})"
      cat "${related_file}" >&2 || true
    else
      log_info "OK  POST /api/videos/related (flag off / not deployed → 404)"
    fi
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
    log_info "Running HTTP smoke tests against ${SERVICE_URL}"
    run_http_smoke "${SERVICE_URL}"
  fi
else
  log_warn "Cloud Run service ${SERVICE} not deployed yet (run ./scripts/gcp/deploy.sh)"
fi

if [[ "${FAILURES}" -gt 0 ]]; then
  log_error "Verification finished with ${FAILURES} failure(s)"
  exit 1
fi

log_info "Verification passed."
