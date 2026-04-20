"""
Integration tests for persistence layer — requires test DB.
Run with: make test-integration (spins up docker-compose.test.yml)
"""

import pytest
from unittest.mock import patch

# ── These tests are marked as requiring the test DB ───────────────────────────
# Skip gracefully if test DB is not available

pytestmark = pytest.mark.skipif(
    __import__("os").environ.get("TEST_DB_AVAILABLE", "0") != "1",
    reason="Requires TEST_DB_AVAILABLE=1 (run: make test-integration)",
)


@pytest.fixture(autouse=True)
def clean_tables(db_session):
    """Clean test data after each test."""
    yield
    from app.models import Review, PullRequest, Repository, GitHubAppConfig, ReviewIssue
    db_session.query(ReviewIssue).delete()
    db_session.query(Review).delete()
    db_session.query(PullRequest).delete()
    db_session.query(Repository).delete()
    db_session.query(GitHubAppConfig).delete()
    db_session.commit()


# ── Repository ─────────────────────────────────────────────────────────────────

def test_upsert_repository_creates_record(db_session):
    from app.persistence import upsert_repository
    upsert_repository(
        installation_id=999,
        full_name="testowner/testrepo",
        default_branch="main",
    )
    db_session.commit()

    from app.models import Repository
    repo = db_session.query(Repository).filter_by(full_name="testowner/testrepo").first()
    assert repo is not None
    assert repo.owner == "testowner"
    assert repo.name == "testrepo"
    assert repo.installation_id == 999


def test_upsert_repository_idempotent(db_session):
    from app.persistence import upsert_repository
    upsert_repository(installation_id=999, full_name="testowner/testrepo")
    upsert_repository(installation_id=999, full_name="testowner/testrepo")
    db_session.commit()

    from app.models import Repository
    count = db_session.query(Repository).filter_by(full_name="testowner/testrepo").count()
    assert count == 1


# ── GitHub App Config ─────────────────────────────────────────────────────────

def test_save_and_load_github_app_config(db_session):
    from app.persistence import save_github_app_config, load_github_app_config
    save_github_app_config(
        app_id="12345",
        app_slug="test-app",
        private_key="-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
        webhook_secret="test-secret-abc",
        client_id="client_abc",
        client_secret="secret_abc",
    )
    db_session.commit()

    cfg = load_github_app_config()
    assert cfg is not None
    assert cfg.app_id == "12345"
    assert cfg.app_slug == "test-app"
    assert cfg.webhook_secret == "test-secret-abc"


def test_save_installation_id(db_session):
    from app.persistence import save_github_app_config, save_github_installation_id, load_github_app_config
    save_github_app_config(
        app_id="12345",
        app_slug="test-app",
        private_key="test-key",
        webhook_secret="test-secret",
    )
    db_session.commit()

    save_github_installation_id(installation_id=888)
    db_session.commit()

    cfg = load_github_app_config()
    assert cfg.installation_id == 888


# ── Review + PullRequest persistence ─────────────────────────────────────────

def test_save_review_creates_related_records(db_session):
    from app.persistence import upsert_repository, save_pull_request, save_review
    upsert_repository(installation_id=999, full_name="testowner/testrepo")
    db_session.commit()

    pr_id = save_pull_request(
        repo_full_name="testowner/testrepo",
        pr_number=42,
        title="Test PR",
        author="testuser",
        head_branch="feature/TEST-42",
        additions=10,
        deletions=2,
        changed_files=1,
    )
    db_session.commit()
    assert pr_id is not None

    save_review(
        pull_request_id=pr_id,
        classification="moderate",
        approved=True,
        plane_state="qa_testing",
        summary="Test review summary",
        suggestion="Add more tests",
        issues=[
            {"severity": "medium", "file": "app/test.py", "line": 10, "comment": "Test issue"},
        ],
    )
    db_session.commit()

    from app.models import Review, ReviewIssue
    review = db_session.query(Review).filter_by(pull_request_id=pr_id).first()
    assert review is not None
    assert review.classification == "moderate"
    assert review.approved is True

    issue = db_session.query(ReviewIssue).filter_by(review_id=review.id).first()
    assert issue is not None
    assert issue.severity == "medium"
    assert issue.file_path == "app/test.py"
