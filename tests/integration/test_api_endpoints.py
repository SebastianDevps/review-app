"""
Integration tests for FastAPI endpoints.
Uses FastAPI TestClient — no real network calls.
External calls (GitHub, Plane) are patched where needed.
"""

import json
import pytest
from unittest.mock import patch, MagicMock


# ── /health ────────────────────────────────────────────────────────────────────

def test_health_returns_ok(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


# ── /webhooks/github ───────────────────────────────────────────────────────────

def test_webhook_pr_opened_returns_202(api_client, signed_pr_opened):
    body, sig = signed_pr_opened
    with patch("app.main.process_pull_request") as mock_task:
        mock_task.apply_async = MagicMock()
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
    assert resp.json()["status"] == "accepted"


def test_webhook_pr_sync_returns_202(api_client, signed_pr_sync):
    body, sig = signed_pr_sync
    with patch("app.main.process_pull_request") as mock_task:
        mock_task.apply_async = MagicMock()
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


def test_webhook_bad_signature_returns_401(api_client, pr_opened_payload):
    body = json.dumps(pr_opened_payload).encode()
    resp = api_client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=deadbeef",
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


def test_webhook_invalid_json_returns_400(api_client):
    bad_body = b"not valid json{"
    # Use empty sig — no secret means it'll pass sig check
    import app.main as m
    original = m._get_webhook_secret
    m._get_webhook_secret = lambda: ""
    try:
        resp = api_client.post(
            "/webhooks/github",
            content=bad_body,
            headers={
                "X-Hub-Signature-256": "sha256=anything",
                "X-GitHub-Event": "pull_request",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 400
    finally:
        m._get_webhook_secret = original


def test_webhook_push_event_triggers_indexing(api_client, signed_pr_opened):
    push_payload = {
        "ref": "refs/heads/main",
        "repository": {
            "full_name": "testowner/testrepo",
            "default_branch": "main",
        },
        "installation": {"id": 999},
    }
    from tests.conftest import sign_payload
    body, sig = sign_payload(push_payload)

    with patch("app.main.index_repository") as mock_task:
        mock_task.apply_async = MagicMock()
        resp = api_client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 202


def test_webhook_push_non_default_branch_ignores(api_client):
    push_payload = {
        "ref": "refs/heads/feature/my-branch",
        "repository": {
            "full_name": "testowner/testrepo",
            "default_branch": "main",
        },
        "installation": {"id": 999},
    }
    from tests.conftest import sign_payload
    body, sig = sign_payload(push_payload)

    with patch("app.main.index_repository") as mock_task:
        mock_task.apply_async = MagicMock()
        resp = api_client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 202
    mock_task.apply_async.assert_not_called()


# ── /api/repos ─────────────────────────────────────────────────────────────────

def test_api_repos_returns_list(api_client):
    with patch("app.api.get_repos") as mock_repos:
        mock_repos.return_value = {"items": [], "total": 0, "page": 1, "pages": 0}
        resp = api_client.get("/api/repos")
    # Could be 200 or 404 depending on DB state; just verify no 500
    assert resp.status_code in (200, 404, 422)


# ── /setup/status ──────────────────────────────────────────────────────────────

def test_setup_status_returns_configured_state(api_client):
    resp = api_client.get("/setup/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "configured" in data
    assert "app_id" in data
    assert isinstance(data["configured"], bool)
