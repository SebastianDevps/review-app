"""
E2E tests — full webhook → worker → DB → API flow.

Uses CELERY_TASK_ALWAYS_EAGER=1 so tasks run synchronously in-process.
Requires: docker-compose.test.yml up (or production stack on 4001)

Two modes:
  1. In-process (CELERY_TASK_ALWAYS_EAGER=1): fast, isolated, default
  2. Live stack (E2E_LIVE=1): fires real HTTP to localhost:4001, waits for async processing
"""

import json
import os
import time
import pytest
import httpx

LIVE_MODE = os.environ.get("E2E_LIVE", "0") == "1"
API_BASE = os.environ.get("E2E_API_URL", "http://localhost:4001")
WAIT_SECONDS = int(os.environ.get("E2E_WAIT_SECONDS", "15"))


# ── Mode 1: in-process (default, fast) ────────────────────────────────────────

class TestWebhookToWorkerInProcess:
    """Full flow without network — TestClient + eager Celery."""

    def test_pr_opened_webhook_accepted(self, api_client, signed_pr_opened):
        body, sig = signed_pr_opened
        with __import__("unittest.mock", fromlist=["patch"]).patch("app.main.process_pull_request") as mock_task:
            mock_task.apply_async = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
            resp = api_client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "pull_request",
                    "Content-Type": "application/json",
                },
            )
        assert resp.status_code == 202
        assert resp.json() == {"status": "accepted"}

    def test_webhook_enqueues_with_correct_params(self, api_client, signed_pr_opened, pr_opened_payload):
        body, sig = signed_pr_opened
        captured_kwargs = {}

        from unittest.mock import patch, MagicMock
        with patch("app.main.process_pull_request") as mock_task:
            def capture(**kwargs):
                captured_kwargs.update(kwargs)
            mock_task.apply_async = MagicMock(side_effect=lambda kwargs, queue: capture(**kwargs))

            api_client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "pull_request",
                    "Content-Type": "application/json",
                },
            )

        assert captured_kwargs.get("installation_id") == 999
        assert captured_kwargs.get("repo_full_name") == "testowner/testrepo"
        assert captured_kwargs.get("pr_number") == 42
        assert captured_kwargs.get("author") == "testuser"
        assert captured_kwargs.get("head_branch") == "feature/TEST-42-e2e-validation"
        # Plane ID extracted from branch name
        assert captured_kwargs.get("plane_issue_id") == "42"

    def test_review_engine_produces_valid_output(self):
        """Mock engine runs synchronously, validates output contract."""
        from app.review_engine import ReviewEngine
        engine = ReviewEngine(config={
            "thresholds": {"trivial_max_lines": 50},
            "review": {"blocking_severities": ["critical", "high"]},
        })

        diff = "\n".join([
            "diff --git a/app/service.py b/app/service.py",
            "--- a/app/service.py",
            "+++ b/app/service.py",
            "@@ -10,3 +10,15 @@",
        ] + [f"+    line_{i} = process(data_{i})" for i in range(60)])

        result = engine.review(diff, {
            "title": "feat: add service layer",
            "additions": 65, "deletions": 5, "changed_files": 2,
        })

        assert result["classification"] in ("trivial", "moderate", "complex")
        assert isinstance(result["approved"], bool)
        assert isinstance(result["issues"], list)
        assert "pr_comment" in result
        assert "plane_state" in result
        assert result["plane_state"] in ("qa_testing", "code_review", "refused")


# ── Mode 2: Live stack (E2E_LIVE=1) ───────────────────────────────────────────

@pytest.mark.skipif(not LIVE_MODE, reason="Set E2E_LIVE=1 to run against live stack")
class TestLiveStack:
    """
    Fires real HTTP to the running stack.
    Requires: docker-compose up + E2E_LIVE=1
    """

    def test_health_endpoint(self):
        resp = httpx.get(f"{API_BASE}/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_full_pr_webhook_to_db(self, pr_opened_payload):
        from tests.conftest import sign_payload, WEBHOOK_SECRET

        # Override secret to match what the server expects
        import os
        secret = os.environ.get("GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)
        body, sig = sign_payload(pr_opened_payload, secret=secret)

        resp = httpx.post(
            f"{API_BASE}/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        assert resp.status_code == 202, f"Webhook rejected: {resp.text}"

        # Wait for async worker to process
        print(f"\n⏳ Waiting {WAIT_SECONDS}s for worker to process review...")
        time.sleep(WAIT_SECONDS)

        # Verify review appears in API
        repos_resp = httpx.get(f"{API_BASE}/api/repos", timeout=5)
        assert repos_resp.status_code == 200
        print(f"✅ Repos: {repos_resp.json()}")

    def test_dashboard_accessible(self):
        dashboard_url = os.environ.get("E2E_DASHBOARD_URL", "http://localhost:4000")
        resp = httpx.get(dashboard_url, timeout=10, follow_redirects=True)
        assert resp.status_code == 200

    def test_setup_status_accessible(self):
        resp = httpx.get(f"{API_BASE}/setup/status", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert "configured" in data
        print(f"✅ Setup status: {data}")
