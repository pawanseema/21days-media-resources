#!/usr/bin/env bash
# Measure Cloud Run cold-start time-to-healthy via /health.
#
# Probes /health, waits INTERVAL seconds on failure, repeats until HTTP 200,
# then prints wall-clock time from the first attempt until success.
#
# Usage:
#   ./scripts/gcp/probe_health_latency.sh
#   ./scripts/gcp/probe_health_latency.sh --url https://….run.app
#   ./scripts/gcp/probe_health_latency.sh --interval 30
#   ./scripts/gcp/probe_health_latency.sh --idle 300   # wait first to encourage scale-to-zero
#   ./scripts/gcp/probe_health_latency.sh --timeout 600

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

INTERVAL_SEC=30
IDLE_SEC=0
TIMEOUT_SEC=900
BASE_URL=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Probe /health until HTTP 200, waiting INTERVAL seconds between failed attempts.
Prints how long cold start / activation took (wall clock from first probe).

Options:
  --url URL         Service base URL (default: describe Cloud Run SERVICE)
  --interval SEC    Seconds between failed probes (default: ${INTERVAL_SEC})
  --idle SEC        Sleep before the first probe to encourage scale-to-zero (default: 0)
  --timeout SEC     Give up after this many seconds of probing (default: ${TIMEOUT_SEC})
  -h, --help        Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) BASE_URL="$2"; shift 2 ;;
    --interval) INTERVAL_SEC="$2"; shift 2 ;;
    --idle) IDLE_SEC="$2"; shift 2 ;;
    --timeout) TIMEOUT_SEC="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) log_error "Unknown option: $1"; usage; exit 1 ;;
  esac
done

require_cmd curl
require_cmd python3
load_config
gcloud_project

if [[ -z "${BASE_URL}" ]]; then
  require_cmd gcloud
  BASE_URL="$(gcloud run services describe "${SERVICE}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --format='value(status.url)')"
fi

BASE_URL="${BASE_URL%/}"
HEALTH_URL="${BASE_URL}/health"

log_info "Probing ${HEALTH_URL} until healthy"
log_info "interval=${INTERVAL_SEC}s timeout=${TIMEOUT_SEC}s idle_before_first=${IDLE_SEC}s"

if [[ "${IDLE_SEC}" -gt 0 ]]; then
  log_info "Sleeping ${IDLE_SEC}s to encourage scale-to-zero / cold path…"
  log_info "(Leave this running; Ctrl+C cancels before any /health probes.)"
  remaining="${IDLE_SEC}"
  while [[ "${remaining}" -gt 0 ]]; do
    chunk=30
    if [[ "${remaining}" -lt "${chunk}" ]]; then
      chunk="${remaining}"
    fi
    sleep "${chunk}"
    remaining=$((remaining - chunk))
    if [[ "${remaining}" -gt 0 ]]; then
      log_info "  … ${remaining}s left before first /health probe"
    fi
  done
  log_info "Idle finished; starting probes."
fi

# Portable epoch seconds (macOS date lacks %N).
now_epoch() {
  python3 -c 'import time; print(int(time.time()))'
}

START_EPOCH="$(now_epoch)"
attempt=0

while true; do
  attempt=$((attempt + 1))
  elapsed=$(( $(now_epoch) - START_EPOCH ))
  if [[ "${elapsed}" -ge "${TIMEOUT_SEC}" ]]; then
    log_error "Timed out after ${elapsed}s (${attempt} attempts) without HTTP 200"
    exit 1
  fi

  # curl -w needs a trailing newline so parsing stays reliable under set -e.
  # Failures (timeouts, connection refused during cold start) must not abort the loop.
  set +e
  curl_out="$(
    curl -sS -o /tmp/probe_health_body.$$ \
      -w "%{http_code} %{time_total}\n" \
      --connect-timeout 30 \
      --max-time 120 \
      "${HEALTH_URL}" 2>/tmp/probe_health_err.$$
  )"
  curl_rc=$?
  set -e

  if [[ "${curl_rc}" -ne 0 ]]; then
    err="$(tr '\n' ' ' </tmp/probe_health_err.$$ 2>/dev/null || true)"
    rm -f /tmp/probe_health_body.$$ /tmp/probe_health_err.$$
    log_warn "attempt ${attempt}: curl failed (rc=${curl_rc}) after ${elapsed}s wall — ${err:-no details}"
    log_info "Waiting ${INTERVAL_SEC}s before next probe…"
    sleep "${INTERVAL_SEC}"
    continue
  fi

  http_code="$(awk '{print $1}' <<<"${curl_out}")"
  time_total="$(awk '{print $2}' <<<"${curl_out}")"
  ms="$(python3 -c "print(int(round(float('${time_total}') * 1000)))")"
  body_preview="$(head -c 120 /tmp/probe_health_body.$$ 2>/dev/null || true)"
  rm -f /tmp/probe_health_body.$$ /tmp/probe_health_err.$$

  if [[ "${http_code}" == "200" ]]; then
    total_sec=$(( $(now_epoch) - START_EPOCH ))
    log_info "attempt ${attempt}: HTTP ${http_code}  request=${ms} ms  wall=${total_sec}s"
    echo
    echo "--- cold start / time-to-healthy ---"
    echo "attempts=${attempt}"
    echo "time_to_healthy_sec=${total_sec}"
    echo "time_to_healthy_ms=$((total_sec * 1000))"
    echo "successful_request_ms=${ms}"
    echo "body_preview=${body_preview}"
    if [[ "${attempt}" -eq 1 && "${ms}" -lt 800 ]]; then
      echo "note: first probe was already fast — instance was likely warm (use --idle 300 to force cold-ish start)"
    elif [[ "${total_sec}" -ge 30 || "${ms}" -ge 1500 ]]; then
      echo "note: elevated time-to-healthy is consistent with cold start / scale-to-zero"
    fi
    exit 0
  fi

  log_warn "attempt ${attempt}: HTTP ${http_code}  request=${ms} ms  wall=${elapsed}s — not healthy yet"
  log_info "Waiting ${INTERVAL_SEC}s before next probe…"
  sleep "${INTERVAL_SEC}"
done
