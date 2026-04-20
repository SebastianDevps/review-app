"""
Unit tests for webhook utility functions — no network, no DB.
Tests: signature verification, Plane ID extraction from branch names.
"""

import hashlib
import hmac
import pytest

from app.main import _extract_plane_id, _verify_signature


# ── _extract_plane_id ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("branch,expected", [
    ("feature/TEST-42-add-endpoint", "42"),
    ("fix/PROJECT-123-null-pointer", "123"),
    ("feat/ABC-1-init", "1"),
    ("main", None),
    ("hotfix/no-ticket", None),
    ("release/v1.2.3", None),
    ("feature/JIRA-9999-big-number", "9999"),
    ("ABC-007", "007"),          # leading zeros preserved (string match)
    ("abc-42",  "42"),           # lowercase prefix
])
def test_extract_plane_id(branch, expected):
    assert _extract_plane_id(branch) == expected


# ── _verify_signature ─────────────────────────────────────────────────────────

SECRET = "test-webhook-secret-e2e"


def _make_sig(payload: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("latin-1"), payload, hashlib.sha256).hexdigest()


def test_valid_signature_passes():
    payload = b'{"action":"opened"}'
    sig = _make_sig(payload)
    # Patch settings to use our test secret
    import app.main as main_module
    original = main_module._get_webhook_secret
    main_module._get_webhook_secret = lambda: SECRET
    try:
        assert _verify_signature(payload, sig) is True
    finally:
        main_module._get_webhook_secret = original


def test_invalid_signature_fails():
    payload = b'{"action":"opened"}'
    bad_sig = "sha256=0000000000000000000000000000000000000000000000000000000000000000"
    import app.main as main_module
    original = main_module._get_webhook_secret
    main_module._get_webhook_secret = lambda: SECRET
    try:
        assert _verify_signature(payload, bad_sig) is False
    finally:
        main_module._get_webhook_secret = original


def test_missing_signature_fails():
    assert _verify_signature(b"payload", "") is False


def test_wrong_prefix_fails():
    payload = b"payload"
    sig = "sha1=" + hmac.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()
    import app.main as main_module
    original = main_module._get_webhook_secret
    main_module._get_webhook_secret = lambda: SECRET
    try:
        assert _verify_signature(payload, sig) is False
    finally:
        main_module._get_webhook_secret = original


def test_tampered_payload_fails():
    original_payload = b'{"action":"opened"}'
    sig = _make_sig(original_payload)
    tampered_payload = b'{"action":"deleted"}'
    import app.main as main_module
    original = main_module._get_webhook_secret
    main_module._get_webhook_secret = lambda: SECRET
    try:
        assert _verify_signature(tampered_payload, sig) is False
    finally:
        main_module._get_webhook_secret = original


def test_no_secret_configured_allows_through():
    """When no secret is set, webhook is allowed (initial setup)."""
    import app.main as main_module
    original = main_module._get_webhook_secret
    main_module._get_webhook_secret = lambda: ""
    try:
        result = _verify_signature(b"anything", "sha256=whatever")
        assert result is True  # graceful during setup
    finally:
        main_module._get_webhook_secret = original
