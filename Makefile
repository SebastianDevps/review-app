# =============================================================================
# Review App — Development & Testing Makefile
# =============================================================================

.PHONY: help up down logs build restart test test-unit test-integration test-e2e test-all lint fmt

# ── Variables ──────────────────────────────────────────────────────────────────
DC      = docker-compose
DC_TEST = docker-compose -f docker-compose.test.yml
PYTEST  = python -m pytest
RUFF    = python -m ruff

# ── Default target ─────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  Review App — Available Commands"
	@echo "  ─────────────────────────────────────────"
	@echo "  make up              Start all services (dev)"
	@echo "  make down            Stop all services"
	@echo "  make logs            Tail all logs"
	@echo "  make build           Rebuild all images"
	@echo "  make restart         Rebuild + restart all"
	@echo ""
	@echo "  make test            Run unit tests (fast, no Docker needed)"
	@echo "  make test-unit       Run unit tests only"
	@echo "  make test-integration Run integration tests (needs test DB)"
	@echo "  make test-e2e        Run E2E bash script against live stack"
	@echo "  make test-e2e-live   Run pytest E2E suite against live stack"
	@echo "  make test-all        Run all test layers"
	@echo ""
	@echo "  make lint            Lint with ruff"
	@echo "  make fmt             Format with ruff"
	@echo ""

# ── Dev stack ──────────────────────────────────────────────────────────────────
up:
	$(DC) up -d

down:
	$(DC) down

logs:
	$(DC) logs -f

build:
	$(DC) build

restart: build up

# ── Unit tests (no external dependencies) ─────────────────────────────────────
test: test-unit

test-unit:
	@echo "\n🧪 Running unit tests..."
	MOCK_AI=true \
	PLANE_API_KEY=test \
	GITHUB_APP_PRIVATE_KEY="" \
	$(PYTEST) tests/unit/ -v --tb=short -x
	@echo "✅ Unit tests complete\n"

# ── Integration tests (requires test DB) ──────────────────────────────────────
test-integration:
	@echo "\n🔗 Starting test infrastructure..."
	$(DC_TEST) up -d
	@echo "⏳ Waiting for DB to be ready..."
	@sleep 5
	@echo "\n🧪 Running integration tests..."
	TEST_DB_AVAILABLE=1 \
	MOCK_AI=true \
	PLANE_API_KEY=test \
	GITHUB_APP_PRIVATE_KEY="" \
	DATABASE_URL=postgresql+psycopg2://reviewapp:reviewapp@localhost:5433/reviewapp_test \
	REDIS_URL=redis://localhost:6380/0 \
	CELERY_BROKER_URL=redis://localhost:6380/0 \
	CELERY_RESULT_BACKEND=redis://localhost:6380/1 \
	CELERY_TASK_ALWAYS_EAGER=1 \
	$(PYTEST) tests/integration/ -v --tb=short
	@echo "✅ Integration tests complete"
	$(DC_TEST) down
	@echo ""

# ── E2E bash script (against live stack) ──────────────────────────────────────
test-e2e:
	@echo "\n🌐 Running E2E test script against live stack..."
	bash scripts/test_e2e.sh

# ── E2E pytest suite (live mode) ──────────────────────────────────────────────
test-e2e-live:
	@echo "\n🌐 Running pytest E2E suite (live stack)..."
	E2E_LIVE=1 \
	MOCK_AI=true \
	PLANE_API_KEY=test \
	GITHUB_APP_PRIVATE_KEY="" \
	$(PYTEST) tests/e2e/ -v --tb=short -s

# ── All tests ─────────────────────────────────────────────────────────────────
test-all: test-unit test-integration test-e2e
	@echo "\n🎉 All test layers complete!\n"

# ── Code quality ──────────────────────────────────────────────────────────────
lint:
	$(RUFF) check app/ tests/

fmt:
	$(RUFF) format app/ tests/
	$(RUFF) check --fix app/ tests/

# ── DB utilities ──────────────────────────────────────────────────────────────
db-reset:
	$(DC) exec postgres psql -U reviewapp -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
	@echo "✅ DB reset complete"

db-shell:
	$(DC) exec postgres psql -U reviewapp -d reviewapp
