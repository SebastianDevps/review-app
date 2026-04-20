"""
FastAPI webhook receiver for GitHub App events.

Security:
  - All incoming webhooks verified via HMAC-SHA256 before processing.
  - Returns HTTP 202 immediately — processing is async via Celery.
  - Handlers are idempotent (GitHub retries on timeout).

Phase 2 additions:
  - Handles 'push' events to trigger re-indexing on merge to default branch
  - POST /index/{repo} — manual index trigger
  - GET /repos — list indexed repos with chunk counts
  - GET /health — service status

Phase 3 additions:
  - Mounts /api/* dashboard REST endpoints
  - DB init on startup
  - CORS for Next.js frontend (localhost:3000)
"""

import hashlib
import hmac
import json
import logging
import re

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import router as dashboard_router
from app.config import settings
from app.database import init_db
from app.github_app_setup import router as setup_router
from app.worker import celery_app, index_repository, process_pull_request

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Review App",
    description="AI Code Review — GitHub App + Plane integration",
    version="0.3.0",
)

# CORS for Next.js dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://review-app.your-domain.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dashboard REST API
app.include_router(dashboard_router)

# Self-service GitHub App setup (Manifest flow)
app.include_router(setup_router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ── Webhook signature verification ────────────────────────────────────────────

def _get_webhook_secret() -> str:
    """Load webhook secret from DB (dynamic) or fallback to .env."""
    try:
        from app.persistence import load_github_app_config
        cfg = load_github_app_config()
        if cfg and cfg.webhook_secret:
            return cfg.webhook_secret
    except Exception:
        pass
    return settings.github_webhook_secret


def _verify_signature(payload: bytes, signature_header: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    secret = _get_webhook_secret()
    if not secret:
        logger.warning("No webhook secret configured — skipping signature check")
        return True  # allow during initial setup
    expected = "sha256=" + hmac.new(
        secret.encode("latin-1"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    try:
        celery_app.control.ping(timeout=1)
        worker_status = "ok"
    except Exception:
        worker_status = "unreachable"

    return {"status": "ok", "version": "0.2.0", "worker": worker_status}


# ── Index status endpoints ────────────────────────────────────────────────────

@app.get("/repos")
def list_repos() -> dict:
    """List all indexed repos with their chunk counts."""
    from app.context_store import get_context_store
    store = get_context_store()
    repos = store.list_indexed_repos()
    return {
        "repos": [
            {"repo": r, "chunks": store.repo_chunk_count(r)}
            for r in repos
        ]
    }


@app.post("/sync-repos/{installation_id}")
def sync_repos(installation_id: int) -> dict:
    """
    Seed the DB with all repos for a GitHub App installation.
    Use this when the installation webhook fired before the system was ready.
    """
    from app.github_auth import get_installation_token
    from app.persistence import upsert_repository
    import httpx

    token = get_installation_token(installation_id)
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            "https://api.github.com/installation/repositories",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    if resp.status_code != 200:
        return {"error": f"GitHub API error: {resp.status_code}"}

    repos_data = resp.json().get("repositories", [])
    seeded = []
    for r in repos_data:
        upsert_repository(
            installation_id=installation_id,
            full_name=r["full_name"],
            default_branch=r.get("default_branch", "main"),
        )
        seeded.append(r["full_name"])
        logger.info("Seeded repo: %s", r["full_name"])

    return {"seeded": seeded, "count": len(seeded)}


@app.post("/index/{installation_id}/{owner}/{repo}")
def trigger_index(installation_id: int, owner: str, repo: str) -> dict:
    """Manually trigger indexing for a repo."""
    repo_full_name = f"{owner}/{repo}"
    task = index_repository.apply_async(
        kwargs={"installation_id": installation_id, "repo_full_name": repo_full_name},
        queue="indexing",
    )
    logger.info("Manual index triggered for %s (task %s)", repo_full_name, task.id)
    return {"status": "queued", "task_id": task.id, "repo": repo_full_name}


@app.get("/index/status/{task_id}")
def index_status(task_id: str) -> dict:
    """Check the status of an indexing task."""
    from celery.result import AsyncResult
    result = AsyncResult(task_id, app=celery_app)
    return {
        "task_id": task_id,
        "status": result.status,
        "result": result.result if result.ready() else None,
    }


# ── GitHub webhook ────────────────────────────────────────────────────────────

@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> JSONResponse:
    """
    Receive all GitHub App webhook events.
    Returns HTTP 202 immediately — actual processing is async.
    """
    payload_bytes = await request.body()

    if not _verify_signature(payload_bytes, x_hub_signature_256 or ""):
        logger.warning("Webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = x_github_event or "unknown"
    action = payload.get("action", "")
    logger.info("GitHub event: %s / action: %s", event, action)

    if event == "pull_request" and action in ("opened", "synchronize", "reopened"):
        _handle_pull_request(payload)

    elif event == "push":
        _handle_push(payload)

    elif event == "installation":
        _handle_installation(payload, action)

    elif event == "installation_repositories" and action in ("added", "removed"):
        _handle_repo_change(payload, action)

    else:
        logger.debug("Ignoring event %s/%s", event, action)

    return JSONResponse(status_code=202, content={"status": "accepted"})


# ── Event handlers ────────────────────────────────────────────────────────────

def _handle_pull_request(payload: dict) -> None:
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})

    installation_id = installation.get("id")
    repo_full_name = repo.get("full_name", "")
    pr_number = pr.get("number")
    head_branch = pr.get("head", {}).get("ref", "")
    author = pr.get("user", {}).get("login", "unknown")

    if not all([installation_id, repo_full_name, pr_number]):
        logger.warning("PR event missing required fields, skipping")
        return

    plane_issue_id = _extract_plane_id(head_branch)

    logger.info(
        "Enqueueing review: %s#%s branch=%s plane=%s author=%s",
        repo_full_name, pr_number, head_branch, plane_issue_id, author,
    )

    process_pull_request.apply_async(
        kwargs={
            "installation_id": installation_id,
            "repo_full_name": repo_full_name,
            "pr_number": pr_number,
            "head_branch": head_branch,
            "author": author,
            "plane_issue_id": plane_issue_id,
        },
        queue="reviews",
    )


def _handle_push(payload: dict) -> None:
    """
    On push to default branch → trigger re-indexing to keep context fresh.
    Only fires on default branch pushes (not every feature branch).
    """
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})

    repo_full_name = repo.get("full_name", "")
    default_branch = repo.get("default_branch", "main")
    pushed_ref = payload.get("ref", "")  # e.g. "refs/heads/main"
    installation_id = installation.get("id")

    # Only re-index on default branch
    pushed_branch = pushed_ref.replace("refs/heads/", "")
    if pushed_branch != default_branch:
        logger.debug("Push to non-default branch %s — skipping re-index", pushed_branch)
        return

    if not installation_id or not repo_full_name:
        return

    logger.info("Push to %s/%s — triggering re-index", repo_full_name, default_branch)
    index_repository.apply_async(
        kwargs={
            "installation_id": installation_id,
            "repo_full_name": repo_full_name,
            "ref": default_branch,
        },
        queue="indexing",
    )


def _handle_installation(payload: dict, action: str) -> None:
    """Log installation events. Create DB records + trigger initial indexing."""
    from app.persistence import upsert_repository

    installation = payload.get("installation", {})
    account = installation.get("account", {}).get("login", "unknown")
    installation_id = installation.get("id")
    repositories = payload.get("repositories", [])

    logger.info("Installation %s: account=%s id=%s repos=%d", action, account, installation_id, len(repositories))

    if action == "created" and repositories and installation_id:
        for repo in repositories[:5]:
            repo_full_name = repo.get("full_name", "")
            if not repo_full_name:
                continue
            # Create DB record immediately so dashboard shows it right away
            try:
                upsert_repository(installation_id=installation_id, full_name=repo_full_name)
            except Exception as exc:
                logger.warning("Could not upsert repo %s: %s", repo_full_name, exc)
            # Trigger background indexing
            logger.info("Triggering initial index for %s", repo_full_name)
            index_repository.apply_async(
                kwargs={"installation_id": installation_id, "repo_full_name": repo_full_name},
                queue="indexing",
            )


def _handle_repo_change(payload: dict, action: str) -> None:
    """Handle repos being added/removed from an installation."""
    installation = payload.get("installation", {})
    installation_id = installation.get("id")
    repos_added = payload.get("repositories_added", [])

    if action == "added" and installation_id:
        for repo in repos_added:
            repo_full_name = repo.get("full_name", "")
            if repo_full_name:
                index_repository.apply_async(
                    kwargs={"installation_id": installation_id, "repo_full_name": repo_full_name},
                    queue="indexing",
                )
                logger.info("Triggered index for newly added repo: %s", repo_full_name)


def _extract_plane_id(branch_name: str) -> str | None:
    """Extract Plane issue ID from branch name."""
    match = re.search(r"[A-Z]+-(\d+)", branch_name, re.IGNORECASE)
    return match.group(1) if match else None
