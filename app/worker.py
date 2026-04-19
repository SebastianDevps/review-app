"""
Celery worker — async task processing for PR reviews and repo indexing.

Three task types:
  1. process_pull_request   — triggered on PR open/sync
  2. index_repository       — triggered on push to default branch (or manual)
  3. cleanup_stale_snapshots — cron every 24h

Pattern: thin webhook receiver (FastAPI, returns 202 immediately) → enqueue → worker.
GitHub expects HTTP 202 within 10s. Reviews take 30-60s, indexing can take minutes.

Persistence: every task saves its results to PostgreSQL so the dashboard shows live data.
"""

import logging

from celery import Celery

from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "review_app",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.worker"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.worker.index_repository": {"queue": "indexing"},
        "app.worker.process_pull_request": {"queue": "reviews"},
        "app.worker.cleanup_stale_snapshots": {"queue": "indexing"},
    },
)


# ── Task 1: Process Pull Request ───────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=3, default_retry_delay=30, queue="reviews")
def process_pull_request(
    self,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    head_branch: str,
    author: str,
    plane_issue_id: str | None = None,
) -> dict:
    """
    Full PR review pipeline with DB persistence:
      1. Ensure repo + PR records exist in DB
      2. Fetch diff + metadata from GitHub
      3. Fetch Plane ticket context
      4. Build semantic context from ChromaDB (hybrid BM25 + vector)
      5. Haiku classifies → Sonnet reviews
      6. Save review to DB (enables dashboard)
      7. Post to GitHub PR
      8. Post to Plane + move state
    """
    from app.context_builder import build_review_context
    from app.context_store import get_context_store
    from app.github_auth import get_pr_diff, get_pr_metadata, post_pr_comment
    from app.persistence import save_review, upsert_pull_request, upsert_repository
    from app.plane_client import PlaneClient
    from app.repo_cloner import get_repo_dir, repo_is_cached
    from app.review_config import load_config
    from app.review_engine import ReviewEngine

    logger.info("Processing PR %s#%s (branch: %s)", repo_full_name, pr_number, head_branch)

    try:
        # ── 1. Ensure repository record exists ───────────────────────────────
        upsert_repository(installation_id=installation_id, full_name=repo_full_name)

        # ── 2. Fetch from GitHub ──────────────────────────────────────────────
        diff = get_pr_diff(installation_id, repo_full_name, pr_number)
        metadata = get_pr_metadata(installation_id, repo_full_name, pr_number)

        # Gate: skip if diff too large
        config = load_config()
        max_lines = config.get("thresholds", {}).get("max_diff_lines", 3000)
        if diff.count("\n") > max_lines:
            logger.info("Diff too large (%d lines), skipping %s#%s", diff.count("\n"), repo_full_name, pr_number)
            return {"skipped": True, "reason": "diff_too_large"}

        # ── 3. Upsert PR record ───────────────────────────────────────────────
        upsert_pull_request(
            full_name=repo_full_name,
            pr_number=pr_number,
            title=metadata.get("title", ""),
            author=author,
            head_branch=head_branch,
            base_branch=metadata.get("base_branch", "main"),
            plane_issue_id=plane_issue_id,
            additions=metadata.get("additions", 0),
            deletions=metadata.get("deletions", 0),
            changed_files=metadata.get("changed_files", 0),
        )

        # ── 4. Fetch Plane ticket context ─────────────────────────────────────
        plane_client = PlaneClient()
        plane_context = ""
        if plane_issue_id:
            plane_context = plane_client.get_ticket_context(plane_issue_id)

        # ── 5. Build semantic context (hybrid BM25 + vector search) ──────────
        store = get_context_store()
        if store.repo_chunk_count(repo_full_name) == 0:
            logger.info("Repo %s not indexed — triggering background index", repo_full_name)
            index_repository.apply_async(
                kwargs={"installation_id": installation_id, "repo_full_name": repo_full_name},
                queue="indexing",
            )

        repo_dir = str(get_repo_dir(repo_full_name)) if repo_is_cached(repo_full_name) else None
        semantic_context = build_review_context(
            repo_full_name=repo_full_name,
            diff=diff,
            plane_context=plane_context,
            repo_dir=repo_dir,
        )

        # ── 6. Run AI review ──────────────────────────────────────────────────
        engine = ReviewEngine(config=config)
        result = engine.review(
            diff=diff,
            pr_metadata=metadata,
            context=semantic_context,
        )

        # ── 7. Persist review to DB (feeds dashboard) ─────────────────────────
        save_review(
            full_name=repo_full_name,
            pr_number=pr_number,
            review_result=result,
        )

        logger.info(
            "Review saved: %s#%s classification=%s approved=%s issues=%d",
            repo_full_name, pr_number,
            result["classification"], result.get("approved"), len(result.get("issues", [])),
        )

        # ── 8. Post to GitHub ─────────────────────────────────────────────────
        if result.get("pr_comment"):
            post_pr_comment(installation_id, repo_full_name, pr_number, result["pr_comment"])

        # ── 9. Post to Plane + move state ────────────────────────────────────
        if plane_issue_id:
            if result.get("plane_comment"):
                plane_client.post_comment(plane_issue_id, result["plane_comment"])
            plane_client.transition_state(plane_issue_id, result.get("plane_state", "code_review"))

        return result

    except Exception as exc:
        logger.exception("PR review failed for %s#%s: %s", repo_full_name, pr_number, exc)
        raise self.retry(exc=exc)


# ── Task 2: Index Repository ───────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=2, default_retry_delay=60, queue="indexing")
def index_repository(
    self,
    installation_id: int,
    repo_full_name: str,
    ref: str = "HEAD",
) -> dict:
    """
    Full repo indexing pipeline:
      1. Upsert repository record in DB
      2. Download repo snapshot via GitHub API (tarball)
      3. Parse all source files with Tree-sitter (44 node types)
      4. Build BM25 index alongside ChromaDB (hybrid search - Gap 1)
      5. Generate project context file (CLAUDE.md equivalent - Gap 3)
      6. Upsert chunks into ChromaDB
      7. Update DB: indexed_at + chunk_count
      8. Clean up local snapshot
    """
    from app.context_store import get_context_store
    from app.indexer import RepoIndexer
    from app.persistence import mark_repository_indexed, upsert_repository
    from app.repo_cloner import cleanup_repo, clone_repo_for_indexing

    logger.info("Indexing %s@%s", repo_full_name, ref)

    try:
        # ── 1. Ensure DB record ───────────────────────────────────────────────
        upsert_repository(installation_id=installation_id, full_name=repo_full_name)

        # ── 2. Download repo snapshot ─────────────────────────────────────────
        repo_dir = clone_repo_for_indexing(installation_id, repo_full_name, ref)

        # ── 3. Parse with Tree-sitter ─────────────────────────────────────────
        indexer = RepoIndexer(repo_full_name)
        chunks = indexer.index(str(repo_dir))

        if not chunks:
            logger.warning("No indexable chunks found in %s", repo_full_name)
            return {"repo": repo_full_name, "chunks": 0, "status": "empty"}

        # ── 4. Upsert into ChromaDB + build BM25 index ────────────────────────
        store = get_context_store()
        store.delete_repo(repo_full_name)
        count = store.upsert_chunks(chunks)           # ChromaDB (vector)
        store.build_bm25_index(repo_full_name, chunks) # BM25 (exact match)

        # ── 5. Generate project context file (Gap 3) ─────────────────────────
        from app.context_generator import generate_project_context
        context_md = generate_project_context(repo_full_name, chunks, str(repo_dir))
        store.save_project_context(repo_full_name, context_md)

        # ── 6. Update DB ──────────────────────────────────────────────────────
        mark_repository_indexed(full_name=repo_full_name, chunk_count=count)

        logger.info("Indexed %s: %d chunks, context generated", repo_full_name, count)
        return {"repo": repo_full_name, "chunks": count, "status": "ok", "ref": ref}

    except Exception as exc:
        logger.exception("Indexing failed for %s: %s", repo_full_name, exc)
        try:
            cleanup_repo(repo_full_name)
        except Exception:
            pass
        raise self.retry(exc=exc)


# ── Task 3: Cleanup stale snapshots ───────────────────────────────────────────

@celery_app.task(queue="indexing")
def cleanup_stale_snapshots() -> dict:
    """Remove local repo snapshots older than 24h."""
    import shutil
    import time
    from app.repo_cloner import REPOS_BASE_DIR

    removed = 0
    now = time.time()

    if not REPOS_BASE_DIR.exists():
        return {"removed": 0}

    for repo_dir in REPOS_BASE_DIR.iterdir():
        if repo_dir.is_dir() and (now - repo_dir.stat().st_mtime) > 86400:
            shutil.rmtree(repo_dir)
            removed += 1
            logger.info("Cleaned up stale snapshot: %s", repo_dir.name)

    return {"removed": removed}


celery_app.conf.beat_schedule = {
    "cleanup-stale-snapshots": {
        "task": "app.worker.cleanup_stale_snapshots",
        "schedule": 86400,
    },
}
