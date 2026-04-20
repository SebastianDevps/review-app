"""
Shared pytest fixtures for all test layers.

Fixtures:
  - db_session         : SQLAlchemy session against test DB (integration)
  - test_client        : FastAPI TestClient with mocked external calls
  - webhook_payload    : helper to build signed webhook payloads
  - mock_github_api    : patches all outgoing GitHub API calls
  - mock_plane_api     : patches all outgoing Plane API calls
"""

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Generator

import pytest

# ── Environment setup (must happen before any app import) ─────────────────────
os.environ.setdefault("MOCK_AI", "true")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://reviewapp:reviewapp@localhost:5433/reviewapp_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6380/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6380/0")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6380/1")
os.environ.setdefault("PLANE_API_KEY", "test-plane-key")
os.environ.setdefault("GITHUB_APP_ID", "9999999")
os.environ.setdefault("GITHUB_APP_PRIVATE_KEY", "")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-webhook-secret-e2e")
os.environ.setdefault("PUBLIC_URL", "https://test.example.com")

FIXTURES_DIR = Path(__file__).parent / "fixtures"
WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"]


# ── Fixture loaders ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def pr_opened_payload() -> dict:
    return json.loads((FIXTURES_DIR / "webhook_pr_opened.json").read_text())


@pytest.fixture(scope="session")
def pr_sync_payload() -> dict:
    return json.loads((FIXTURES_DIR / "webhook_pr_synchronize.json").read_text())


@pytest.fixture(scope="session")
def plane_issue_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "plane_issue.json").read_text())


# ── Webhook signature helper ───────────────────────────────────────────────────

def sign_payload(payload: dict | str, secret: str = WEBHOOK_SECRET) -> tuple[bytes, str]:
    """Return (payload_bytes, sha256_signature) for webhook testing."""
    if isinstance(payload, dict):
        body = json.dumps(payload, separators=(",", ":")).encode()
    else:
        body = payload.encode() if isinstance(payload, str) else payload
    sig = "sha256=" + hmac.new(secret.encode("latin-1"), body, hashlib.sha256).hexdigest()
    return body, sig


@pytest.fixture
def signed_pr_opened(pr_opened_payload) -> tuple[bytes, str]:
    return sign_payload(pr_opened_payload)


@pytest.fixture
def signed_pr_sync(pr_sync_payload) -> tuple[bytes, str]:
    return sign_payload(pr_sync_payload)


# ── FastAPI test client ────────────────────────────────────────────────────────

@pytest.fixture
def api_client():
    """FastAPI TestClient — does NOT hit real GitHub/Plane APIs."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


# ── DB session (requires docker-compose.test.yml running) ─────────────────────

@pytest.fixture(scope="function")
def db_session():
    """
    Provides a clean DB session for each test.
    Rolls back after each test to keep state isolated.
    Requires: docker-compose -f docker-compose.test.yml up -d
    """
    from app.database import engine, SessionLocal
    from app.models import Base

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ── Mock helpers ───────────────────────────────────────────────────────────────

@pytest.fixture
def mock_github_token(monkeypatch):
    """Stub out GitHub App JWT/token generation."""
    monkeypatch.setattr(
        "app.github_auth.get_installation_token",
        lambda installation_id: "ghs_test_token_mock",
    )


@pytest.fixture
def mock_plane_client(monkeypatch, plane_issue_fixture):
    """Stub out Plane API calls."""
    class _MockPlane:
        def get_issue(self, *a, **kw): return plane_issue_fixture
        def update_issue_state(self, *a, **kw): return {"id": "test-issue-uuid-42"}
        def add_comment(self, *a, **kw): return {"id": "comment-uuid"}

    monkeypatch.setattr("app.plane_client.PlaneClient", lambda *a, **kw: _MockPlane())
