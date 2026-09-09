#!/usr/bin/env bash
# 서비스 API curl 스모크 — 시드 DB(make seed) 기준. 실행: bash tools/smoke.sh [BASE_URL]
#   login(root) → me → devices → residents 생성/삭제 → 주 장치 삭제 409 → member config 403 → refresh → logout
set -euo pipefail
BASE="${1:-http://127.0.0.1:8000}"
API="$BASE/api/v1"
J='Content-Type: application/json'

need() { command -v "$1" >/dev/null || { echo "need $1"; exit 1; }; }
need curl; PY=$(command -v python || command -v python3) || { echo "need python or python3"; exit 1; }

jq_() { "$PY" -c "import sys,json; d=json.load(sys.stdin); print(eval('d'+sys.argv[1]))" "$1"; }
step() { printf '\n== %s\n' "$*"; }
expect() { # expect <code> <actual> <label>
  if [ "$1" != "$2" ]; then echo "FAIL: $3 expected $1 got $2"; exit 1; fi; echo "ok  $3 [$2]"; }

step "health"
curl -sf "$BASE/health" | jq_ "['status']"

step "login root@demo.io"
LOGIN=$(curl -s -H "$J" -d '{"email":"root@demo.io","password":"demo"}' "$API/auth/login")
ACCESS=$(echo "$LOGIN" | jq_ "['accessToken']"); REFRESH=$(echo "$LOGIN" | jq_ "['refreshToken']")
AUTH="Authorization: Bearer $ACCESS"

step "me"
curl -s -H "$AUTH" "$API/auth/me" | jq_ "['facility']['name']"

step "devices (seed 8)"
DEVS=$(curl -s -H "$AUTH" "$API/devices"); echo "$DEVS" | "$PY" -c "import sys,json; print(len(json.load(sys.stdin)))"
D6=$(echo "$DEVS" | "$PY" -c "import sys,json; print([x['id'] for x in json.load(sys.stdin) if x['name']=='204호 화장실'][0])")

step "primary device delete -> 409"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE -H "$AUTH" "$API/devices/$D6"); expect 409 "$CODE" "delete primary device"

step "resident create/delete"
R=$(curl -s -H "$J" -H "$AUTH" -d "{\"name\":\"smoke-test\",\"room\":\"999\",\"deviceIds\":[\"$D6\"]}" "$API/residents")
echo "$R" | grep -q '"id"' || { echo "FAIL: resident create -> $R"; exit 1; }
RID=$(echo "$R" | jq_ "['id']"); echo "created $RID primary=$(echo "$R" | jq_ "['deviceId']")"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE -H "$AUTH" "$API/residents/$RID"); expect 204 "$CODE" "delete resident"

step "falls list / event-logs"
curl -s -H "$AUTH" "$API/falls?limit=5" | jq_ "['total']"
curl -s -H "$AUTH" "$API/event-logs?limit=3" | jq_ "['total']"

step "member config PUT -> 403"
MEM=$(curl -s -H "$J" -d '{"email":"member@demo.io","password":"demo"}' "$API/auth/login" | jq_ "['accessToken']")
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H "$J" -H "Authorization: Bearer $MEM" \
  -d '{"presenceMvThreshold":2,"wanderRatioThreshold":1.8,"presenceTimeoutS":10,"threshold":0.468,"cooldownSeconds":10}' "$API/config")
expect 403 "$CODE" "member PUT /config"

step "refresh rotation"
NEW=$(curl -s -H "$J" -d "{\"refreshToken\":\"$REFRESH\"}" "$API/auth/refresh"); NEWR=$(echo "$NEW" | jq_ "['refreshToken']")
CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "$J" -d "{\"refreshToken\":\"$REFRESH\"}" "$API/auth/refresh"); expect 401 "$CODE" "old refresh reused"

step "logout"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$J" -H "Authorization: Bearer $(echo "$NEW" | jq_ "['accessToken']")" -d "{\"refreshToken\":\"$NEWR\"}" "$API/auth/logout")
expect 204 "$CODE" "logout"
echo; echo "SMOKE OK"
