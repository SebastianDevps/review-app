"""
GitHub App authentication.

Two-legged auth flow (no user required):
  1. Generate a short-lived JWT signed with the App's RSA private key
  2. Exchange the JWT for an installation access token (scoped to one org/repo)
  3. Use that token for all GitHub API calls

Reference pattern (inspired by pr-agent/github_app.py architecture):
  https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app
"""

import time
import logging

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from app.config import settings

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def _load_private_key() -> RSAPrivateKey:
    """Load the RSA private key from settings (supports \\n escaped PEM strings)."""
    pem = settings.github_app_private_key.replace("\\n", "\n")
    return serialization.load_pem_private_key(pem.encode(), password=None)  # type: ignore[return-value]


def generate_app_jwt() -> str:
    """
    Generate a 10-minute JWT to authenticate as the GitHub App itself.
    Backdated 60s to account for clock skew between servers.
    """
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,  # 10 minutes
        "iss": settings.github_app_id,
    }
    private_key = _load_private_key()
    return jwt.encode(payload, private_key, algorithm="RS256")  # type: ignore[arg-type]


def get_installation_token(installation_id: int) -> str:
    """
    Exchange a GitHub App JWT for an installation access token.
    Tokens are valid for 1 hour and scoped to the specific installation.

    Args:
        installation_id: The installation ID from the webhook payload.

    Returns:
        A short-lived installation access token string.
    """
    app_jwt = generate_app_jwt()
    url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"

    with httpx.Client(timeout=15) as client:
        response = client.post(
            url,
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    if response.status_code != 201:
        logger.error("Failed to get installation token: %s %s", response.status_code, response.text)
        raise RuntimeError(f"GitHub installation token request failed: {response.status_code}")

    token = response.json()["token"]
    logger.debug("Got installation token for installation %s", installation_id)
    return token


def get_pr_diff(installation_id: int, repo_full_name: str, pr_number: int) -> str:
    """
    Fetch the unified diff for a pull request.

    Args:
        installation_id: GitHub App installation ID.
        repo_full_name: e.g. 'zetainc-co/nellup'
        pr_number: Pull request number.

    Returns:
        Raw unified diff string.
    """
    token = get_installation_token(installation_id)
    url = f"{GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}"

    with httpx.Client(timeout=30) as client:
        response = client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.diff",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    if response.status_code != 200:
        raise RuntimeError(f"Failed to fetch PR diff: {response.status_code}")

    return response.text


def post_pr_comment(installation_id: int, repo_full_name: str, pr_number: int, body: str) -> None:
    """Post a comment on a pull request."""
    token = get_installation_token(installation_id)
    url = f"{GITHUB_API}/repos/{repo_full_name}/issues/{pr_number}/comments"

    with httpx.Client(timeout=15) as client:
        response = client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"body": body},
        )

    if response.status_code not in (200, 201):
        logger.error("Failed to post PR comment: %s %s", response.status_code, response.text)
        raise RuntimeError(f"GitHub comment post failed: {response.status_code}")

    logger.info("Posted review comment on %s#%s", repo_full_name, pr_number)


def get_pr_metadata(installation_id: int, repo_full_name: str, pr_number: int) -> dict:
    """Fetch PR metadata: title, body, author, head branch, base branch."""
    token = get_installation_token(installation_id)
    url = f"{GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}"

    with httpx.Client(timeout=15) as client:
        response = client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    if response.status_code != 200:
        raise RuntimeError(f"Failed to fetch PR metadata: {response.status_code}")

    data = response.json()
    return {
        "title": data.get("title", ""),
        "body": data.get("body", "") or "",
        "author": data.get("user", {}).get("login", "unknown"),
        "head_branch": data.get("head", {}).get("ref", ""),
        "base_branch": data.get("base", {}).get("ref", "main"),
        "additions": data.get("additions", 0),
        "deletions": data.get("deletions", 0),
        "changed_files": data.get("changed_files", 0),
    }
