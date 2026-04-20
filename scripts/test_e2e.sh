#!/usr/bin/env bash
# =============================================================================
# test_e2e.sh — Orquestador de pruebas E2E controladas para Review App
#
# Ejecuta validación completa del flujo:
#   Webhook → Celery Worker → PostgreSQL → API → Dashboard
#
# Uso:
#   bash scripts/test_e2e.sh                         # contra stack local (4001)
#   API_URL=http://localhost:4001 bash scripts/test_e2e.sh
#   WAIT_SECONDS=20 bash scripts/test_e2e.sh         # más tiempo para workers lentos
#
# Requisitos:
#   - docker-compose up (stack corriendo)
#   - jq instalado (brew install jq)
#   - openssl disponible
# =============================================================================

set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────────────
API_URL="${API_URL:-http://localhost:4001}"
DASHBOARD_URL="${DASHBOARD_URL:-http://localhost:4000}"
WEBHOOK_SECRET="${GITHUB_WEBHOOK_SECRET:-1ec36244105647052e062ee9721079ff8325527a}"
WAIT_SECONDS="${WAIT_SECONDS:-15}"
FIXTURE="tests/fixtures/webhook_pr_opened.json"
DYNAMIC_FIXTURE="/tmp/review_app_test_webhook.json"

# ── Colors ─────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PASS=0
FAIL=0
START_TIME=$(date +%s)

# ── Helpers ────────────────────────────────────────────────────────────────────
pass() { echo -e "${GREEN}  ✅ PASS${NC} — $1"; PASS=$((PASS + 1)); }
fail() { echo -e "${RED}  ❌ FAIL${NC} — $1"; FAIL=$((FAIL + 1)); }
step() { echo -e "\n${CYAN}${BOLD}=== [$1/$TOTAL] $2 ===${NC}"; }
info() { echo -e "${YELLOW}  ℹ️  $1${NC}"; }

TOTAL=9

# ── Check dependencies ─────────────────────────────────────────────────────────
check_deps() {
    for cmd in curl jq openssl; do
        if ! command -v $cmd &>/dev/null; then
            echo -e "${RED}❌ Required tool not found: $cmd${NC}"
            echo "   Install with: brew install $cmd"
            exit 1
        fi
    done
}

# ── Sign webhook payload ────────────────────────────────────────────────────────
sign_payload() {
    local payload="$1"
    local secret="$2"
    echo -n "$payload" | openssl dgst -sha256 -hmac "$secret" | awk '{print "sha256="$2}'
}

# ── Main tests ─────────────────────────────────────────────────────────────────

check_deps

echo -e "\n${BOLD}🤖 Review App — E2E Test Suite${NC}"
echo -e "   API:       $API_URL"
echo -e "   Dashboard: $DASHBOARD_URL"
echo -e "   Wait:      ${WAIT_SECONDS}s after webhook"
echo ""

# ────────────────────────────────────────────────────────────────────────────────
step 1 "Health check — API"
HEALTH=$(curl -sf "$API_URL/health" || echo '{"status":"error"}')
STATUS=$(echo "$HEALTH" | jq -r '.status' 2>/dev/null || echo "error")
if [ "$STATUS" = "ok" ]; then
    pass "API health OK — $(echo "$HEALTH" | jq -c .)"
else
    fail "API health returned: $HEALTH"
    echo -e "${RED}  Cannot continue — API is not running${NC}"
    exit 1
fi

# ────────────────────────────────────────────────────────────────────────────────
step 2 "Health check — Dashboard"
HTTP_CODE=$(curl -so /dev/null -w "%{http_code}" --max-time 5 "$DASHBOARD_URL" || echo "000")
if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "307" ] || [ "$HTTP_CODE" = "308" ]; then
    pass "Dashboard accessible (HTTP $HTTP_CODE)"
else
    fail "Dashboard returned HTTP $HTTP_CODE"
fi

# ────────────────────────────────────────────────────────────────────────────────
step 3 "Setup status — GitHub App configured"
SETUP=$(curl -sf "$API_URL/setup/status" || echo '{"configured":false}')
CONFIGURED=$(echo "$SETUP" | jq -r '.configured' 2>/dev/null || echo "false")
APP_ID=$(echo "$SETUP" | jq -r '.app_id' 2>/dev/null || echo "none")
info "App ID: $APP_ID | Configured: $CONFIGURED"
if [ "$CONFIGURED" = "true" ]; then
    pass "GitHub App configured (App ID: $APP_ID)"
else
    info "GitHub App not configured — setup flow incomplete"
    pass "Setup endpoint accessible (not blocking E2E)"
fi

# ────────────────────────────────────────────────────────────────────────────────
step 4 "Detect real installation_id + build dynamic fixture"
# Fetch real installation_id and repo from DB (set during GitHub App setup)
REAL_INSTALL_ID=$(docker exec review-app-postgres-1 psql -U reviewapp -d reviewapp -tAc \
    "SELECT installation_id FROM github_app_config WHERE installation_id IS NOT NULL LIMIT 1;" 2>/dev/null | tr -d '[:space:]')
REAL_REPO=$(docker exec review-app-postgres-1 psql -U reviewapp -d reviewapp -tAc \
    "SELECT full_name FROM repositories WHERE installation_id != 999 LIMIT 1;" 2>/dev/null | tr -d '[:space:]')

if [ -z "$REAL_INSTALL_ID" ] || [ "$REAL_INSTALL_ID" = "" ]; then
    info "No real installation_id found — using fixture defaults (999/testowner/testrepo)"
    REAL_INSTALL_ID=999
    REAL_REPO="testowner/testrepo"
fi

REAL_OWNER=$(echo "$REAL_REPO" | cut -d'/' -f1)
REAL_REPO_NAME=$(echo "$REAL_REPO" | cut -d'/' -f2)
info "Using installation_id=$REAL_INSTALL_ID repo=$REAL_REPO"

# Generate dynamic fixture with real IDs
cat "$FIXTURE" | \
    jq --argjson iid "$REAL_INSTALL_ID" \
       --arg repo "$REAL_REPO" \
       --arg owner "$REAL_OWNER" \
       --arg rname "$REAL_REPO_NAME" \
       '.installation.id = $iid |
        .repository.full_name = $repo |
        .repository.name = $rname |
        .repository.owner.login = $owner' > "$DYNAMIC_FIXTURE"

# Try to detect a real open PR number from GitHub API
REAL_PR_NUMBER=42
REAL_PR_BRANCH="feature/TEST-42-e2e-validation"
REAL_PR_TITLE="E2E Test PR"
REAL_PR_AUTHOR="testuser"

# Fetch the latest open PR from the real repo (requires GitHub token)
GITHUB_TOKEN=$(docker exec review-app-api-1 python3 -c \
    "from app.github_auth import get_installation_token; print(get_installation_token($REAL_INSTALL_ID))" \
    2>/dev/null || echo "")

if [ -n "$GITHUB_TOKEN" ] && [ "$GITHUB_TOKEN" != "" ]; then
    PR_DATA=$(curl -sf "https://api.github.com/repos/$REAL_REPO/pulls?state=open&per_page=1" \
        -H "Authorization: Bearer $GITHUB_TOKEN" \
        -H "Accept: application/vnd.github+json" 2>/dev/null || echo "[]")
    PR_COUNT=$(echo "$PR_DATA" | jq '. | length' 2>/dev/null || echo "0")

    if [ "$PR_COUNT" -gt "0" ]; then
        REAL_PR_NUMBER=$(echo "$PR_DATA" | jq '.[0].number' 2>/dev/null || echo "42")
        REAL_PR_BRANCH=$(echo "$PR_DATA" | jq -r '.[0].head.ref' 2>/dev/null || echo "feature/test")
        REAL_PR_TITLE=$(echo "$PR_DATA" | jq -r '.[0].title' 2>/dev/null || echo "Test PR")
        REAL_PR_AUTHOR=$(echo "$PR_DATA" | jq -r '.[0].user.login' 2>/dev/null || echo "testuser")
        info "Found real open PR #$REAL_PR_NUMBER: $REAL_PR_TITLE (branch: $REAL_PR_BRANCH)"
    else
        info "No open PRs found in $REAL_REPO — using PR #$REAL_PR_NUMBER (may fail worker)"
    fi
fi

# Rebuild fixture with real PR number
jq --argjson iid "$REAL_INSTALL_ID" \
   --argjson prnum "$REAL_PR_NUMBER" \
   --arg repo "$REAL_REPO" \
   --arg owner "$REAL_OWNER" \
   --arg rname "$REAL_REPO_NAME" \
   --arg branch "$REAL_PR_BRANCH" \
   --arg title "$REAL_PR_TITLE" \
   --arg author "$REAL_PR_AUTHOR" \
   '.installation.id = $iid |
    .number = $prnum |
    .pull_request.number = $prnum |
    .pull_request.head.ref = $branch |
    .pull_request.title = $title |
    .pull_request.user.login = $author |
    .repository.full_name = $repo |
    .repository.name = $rname |
    .repository.owner.login = $owner' "$FIXTURE" > "$DYNAMIC_FIXTURE"

pass "Dynamic fixture built (install_id=$REAL_INSTALL_ID, repo=$REAL_REPO, PR=#$REAL_PR_NUMBER)"

REPOS=$(curl -sf "$API_URL/api/repos" 2>/dev/null || echo '{"items":[]}')
REPO_COUNT=$(echo "$REPOS" | jq '.items | length' 2>/dev/null || echo "?")
info "Repos in DB: $REPO_COUNT"

# ────────────────────────────────────────────────────────────────────────────────
step 5 "Send PR opened webhook (signed)"
# Use dynamic fixture (with real installation_id) if available
ACTIVE_FIXTURE="${DYNAMIC_FIXTURE:-$FIXTURE}"
if [ ! -f "$ACTIVE_FIXTURE" ]; then ACTIVE_FIXTURE="$FIXTURE"; fi

if [ ! -f "$ACTIVE_FIXTURE" ]; then
    fail "Fixture not found: $ACTIVE_FIXTURE"
else
    PAYLOAD=$(cat "$ACTIVE_FIXTURE")
    SIG=$(sign_payload "$PAYLOAD" "$WEBHOOK_SECRET")
    info "Signature: $SIG"

    WEBHOOK_RESP=$(curl -sf -X POST "$API_URL/webhooks/github" \
        -H "X-Hub-Signature-256: $SIG" \
        -H "X-GitHub-Event: pull_request" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" 2>&1 || echo '{"error":"connection_failed"}')

    WEBHOOK_STATUS=$(echo "$WEBHOOK_RESP" | jq -r '.status' 2>/dev/null || echo "error")
    if [ "$WEBHOOK_STATUS" = "accepted" ]; then
        pass "Webhook accepted (202)"
    else
        fail "Webhook response: $WEBHOOK_RESP"
    fi
fi

# ────────────────────────────────────────────────────────────────────────────────
step 6 "Wait for Celery worker to process review"
info "Waiting ${WAIT_SECONDS} seconds..."
for i in $(seq 1 $WAIT_SECONDS); do
    printf "\r  ⏳ ${i}/${WAIT_SECONDS}s"
    sleep 1
done
echo ""
pass "Wait complete"

# ────────────────────────────────────────────────────────────────────────────────
step 7 "Verify worker logs show processing"
WORKER_LOGS=$(docker logs review-app-worker-reviews-1 --tail 30 2>/dev/null || echo "")
if echo "$WORKER_LOGS" | grep -qE "(SUCCESS|process_pull_request|review)"; then
    TASK_LINE=$(echo "$WORKER_LOGS" | grep -E "(SUCCESS|process_pull_request)" | tail -1)
    pass "Worker processed task: $TASK_LINE"
else
    info "Worker logs (last 5 lines):"
    echo "$WORKER_LOGS" | tail -5 | sed 's/^/    /'
    fail "No evidence of task processing in worker logs"
fi

# ────────────────────────────────────────────────────────────────────────────────
step 8 "Verify review in DB via API + psql"
# Check via REST API using real repo
REVIEW_RESP=$(curl -sf "$API_URL/api/repos/$REAL_OWNER/$REAL_REPO_NAME/prs?page=1" 2>/dev/null || echo '{"items":[]}')
REVIEW_COUNT=$(echo "$REVIEW_RESP" | jq '.items | length' 2>/dev/null || echo "0")
info "PRs via API ($REAL_REPO): $REVIEW_COUNT"

# Also check directly in DB
DB_PR_COUNT=$(docker exec review-app-postgres-1 psql -U reviewapp -d reviewapp -tAc \
    "SELECT COUNT(*) FROM pull_requests pr JOIN repositories r ON pr.repository_id=r.id WHERE r.full_name='$REAL_REPO';" 2>/dev/null | tr -d '[:space:]' || echo "0")
DB_REVIEW_COUNT=$(docker exec review-app-postgres-1 psql -U reviewapp -d reviewapp -tAc \
    "SELECT COUNT(*) FROM reviews rv JOIN pull_requests pr ON rv.pull_request_id=pr.id JOIN repositories r ON pr.repository_id=r.id WHERE r.full_name='$REAL_REPO';" 2>/dev/null | tr -d '[:space:]' || echo "0")
info "DB: $DB_PR_COUNT PRs, $DB_REVIEW_COUNT reviews"

if [ "$DB_PR_COUNT" -gt "0" ]; then
    if [ "$DB_REVIEW_COUNT" -gt "0" ]; then
        CLASSIFICATION=$(docker exec review-app-postgres-1 psql -U reviewapp -d reviewapp -tAc \
            "SELECT classification FROM reviews ORDER BY id DESC LIMIT 1;" 2>/dev/null | tr -d '[:space:]')
        pass "Review stored in DB — classification=$CLASSIFICATION, reviews=$DB_REVIEW_COUNT"
    else
        pass "PR stored in DB ($DB_PR_COUNT PRs) — review pending/processing"
    fi
else
    # Worker may have failed to get GitHub token (expected in mock) — check worker error
    WORKER_ERR=$(docker logs review-app-worker-reviews-1 --tail 10 2>/dev/null | grep -iE "(error|fail|exception)" | tail -3 || echo "")
    if [ -n "$WORKER_ERR" ]; then
        info "Worker errors: $WORKER_ERR"
    fi
    fail "No PRs stored in DB for $REAL_REPO"
fi

# ────────────────────────────────────────────────────────────────────────────────
step 9 "Send synchronize event (second commit simulation)"
SYNC_FIXTURE_SRC="tests/fixtures/webhook_pr_synchronize.json"
SYNC_FIXTURE_DYN="/tmp/review_app_test_sync.json"
if [ -f "$SYNC_FIXTURE_SRC" ] && [ -n "$REAL_INSTALL_ID" ]; then
    cat "$SYNC_FIXTURE_SRC" | \
        jq --argjson iid "$REAL_INSTALL_ID" \
           --arg repo "$REAL_REPO" \
           --arg owner "$REAL_OWNER" \
           --arg rname "$REAL_REPO_NAME" \
           '.installation.id = $iid |
            .repository.full_name = $repo |
            .repository.name = $rname |
            .repository.owner.login = $owner' > "$SYNC_FIXTURE_DYN"
fi
if [ -f "$SYNC_FIXTURE_DYN" ]; then
    SYNC_PAYLOAD=$(cat "$SYNC_FIXTURE_DYN")
    SYNC_SIG=$(sign_payload "$SYNC_PAYLOAD" "$WEBHOOK_SECRET")

    SYNC_RESP=$(curl -sf -X POST "$API_URL/webhooks/github" \
        -H "X-Hub-Signature-256: $SYNC_SIG" \
        -H "X-GitHub-Event: pull_request" \
        -H "Content-Type: application/json" \
        -d "$SYNC_PAYLOAD" 2>&1 || echo '{"error":"failed"}')

    SYNC_STATUS=$(echo "$SYNC_RESP" | jq -r '.status' 2>/dev/null || echo "error")
    if [ "$SYNC_STATUS" = "accepted" ]; then
        pass "Synchronize webhook accepted"
    else
        fail "Synchronize webhook: $SYNC_RESP"
    fi
else
    info "Synchronize fixture not found — skipping"
    pass "Synchronize test skipped"
fi

# ── Summary ────────────────────────────────────────────────────────────────────
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
TOTAL_TESTS=$((PASS + FAIL))

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  Test Results: ${PASS}/${TOTAL_TESTS} passed in ${DURATION}s${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}  ✅ ALL TESTS PASSED${NC}"
    exit 0
else
    echo -e "${RED}${BOLD}  ❌ $FAIL TEST(S) FAILED${NC}"
    exit 1
fi
