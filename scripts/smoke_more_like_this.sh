#!/usr/bin/env bash
# Smoke-test Phase A: feature flag + /api/videos/related
# Usage (from project root, with Flask running on PORT):
#   ./scripts/smoke_more_like_this.sh
#   PORT=5005 ENABLE_MORE_LIKE_THIS=true ./scripts/smoke_more_like_this.sh
#
# For full on/off check, start Flask twice or set the env when starting the server:
#   ENABLE_MORE_LIKE_THIS=false python api/flask_api_server.py
#   ENABLE_MORE_LIKE_THIS=true  python api/flask_api_server.py

set -euo pipefail

PORT="${PORT:-5005}"
BASE="http://127.0.0.1:${PORT}"

echo "== GET /api/ui-config =="
curl -sS "${BASE}/api/ui-config"
echo
echo

FLAG="$(curl -sS "${BASE}/api/ui-config" | python3 -c "import sys,json; print(json.load(sys.stdin).get('enableMoreLikeThis'))")"
echo "enableMoreLikeThis=${FLAG}"

echo
echo "== POST /api/videos/related (sample seed) =="
HTTP_CODE="$(curl -sS -o /tmp/related_resp.json -w '%{http_code}' \
  -X POST "${BASE}/api/videos/related" \
  -H 'Content-Type: application/json' \
  -d '{"video_id":"CO1vvOCoLjI","timestamp":"4:04","top_k":3}')"
echo "HTTP ${HTTP_CODE}"
python3 -m json.tool /tmp/related_resp.json 2>/dev/null || cat /tmp/related_resp.json
echo

if [[ "${FLAG}" == "True" || "${FLAG}" == "true" ]]; then
  if [[ "${HTTP_CODE}" != "200" ]]; then
    echo "FAIL: expected 200 when flag is on"
    exit 1
  fi
  echo "OK: related endpoint returned results while flag is on"
else
  if [[ "${HTTP_CODE}" != "404" ]]; then
    echo "FAIL: expected 404 when flag is off"
    exit 1
  fi
  echo "OK: related endpoint disabled while flag is off"
fi
