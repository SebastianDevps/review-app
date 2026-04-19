"""
DB persistence helpers — called by Celery workers to save review data.

All functions are synchronous (workers use sync SQLAlchemy).
Each function is idempotent: safe to call multiple times for the same PR/review.
"""

import datetime
import logging

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import PullRequest, Repository, Review, ReviewIssue

logger = logging.getLogger(__name__)


# ── Repository ─────────────────────────────────────────────────────────────────

def upsert_repository(
    installation_id: int,
    full_name: str,
    default_branch: str = "main",
) -> Repository:
    """Create or update a Repository record. Returns the record."""
    owner, name = full_name.split("/", 1)
    with SessionLocal() as db:
        repo = db.query(Repository).filter(Repository.full_name == full_name).first()
        if repo is None:
            repo = Repository(
                installation_id=installation_id,
                full_name=full_name,
                owner=owner,
                name=name,
                default_branch=default_branch,
                is_active=True,
            )
            db.add(repo)
            logger.info("Created repository record: %s", full_name)
        else:
            repo.installation_id = installation_id
            repo.default_branch = default_branch
            repo.is_active = True
        db.commit()
        db.refresh(repo)
        return repo


def mark_repository_indexed(full_name: str, chunk_count: int) -> None:
    """Update indexed_at and chunk_count after a successful index run."""
    with SessionLocal() as db:
        repo = db.query(Repository).filter(Repository.full_name == full_name).first()
        if repo:
            repo.indexed_at = datetime.datetime.utcnow()
            repo.chunk_count = chunk_count
            db.commit()
            logger.info("Marked %s as indexed (%d chunks)", full_name, chunk_count)


# ── Pull Request ───────────────────────────────────────────────────────────────

def upsert_pull_request(
    full_name: str,
    pr_number: int,
    title: str = "",
    author: str = "",
    head_branch: str = "",
    base_branch: str = "main",
    plane_issue_id: str | None = None,
    additions: int = 0,
    deletions: int = 0,
    changed_files: int = 0,
) -> PullRequest | None:
    """
    Create or update a PullRequest record.
    Requires the Repository to already exist (call upsert_repository first).
    """
    with SessionLocal() as db:
        repo = db.query(Repository).filter(Repository.full_name == full_name).first()
        if repo is None:
            logger.warning("Cannot upsert PR — repository %s not found in DB", full_name)
            return None

        pr = (
            db.query(PullRequest)
            .filter(PullRequest.repository_id == repo.id, PullRequest.pr_number == pr_number)
            .first()
        )

        if pr is None:
            pr = PullRequest(
                repository_id=repo.id,
                pr_number=pr_number,
                title=title,
                author=author,
                head_branch=head_branch,
                base_branch=base_branch,
                plane_issue_id=plane_issue_id,
                additions=additions,
                deletions=deletions,
                changed_files=changed_files,
            )
            db.add(pr)
            logger.info("Created PR record: %s#%s", full_name, pr_number)
        else:
            # Update fields that can change on synchronize event
            pr.title = title or pr.title
            pr.additions = additions
            pr.deletions = deletions
            pr.changed_files = changed_files

        db.commit()
        db.refresh(pr)
        return pr


# ── Review ─────────────────────────────────────────────────────────────────────

def save_review(
    full_name: str,
    pr_number: int,
    review_result: dict,
) -> Review | None:
    """
    Save or replace the AI review result for a PR.
    If a review already exists for this PR, it is replaced (idempotent re-run).

    Args:
        full_name: repo full name e.g. 'zetainc-co/nellup'
        pr_number: GitHub PR number
        review_result: dict returned by ReviewEngine.review()
    """
    with SessionLocal() as db:
        repo = db.query(Repository).filter(Repository.full_name == full_name).first()
        if repo is None:
            logger.warning("Cannot save review — repository %s not in DB", full_name)
            return None

        pr = (
            db.query(PullRequest)
            .filter(PullRequest.repository_id == repo.id, PullRequest.pr_number == pr_number)
            .first()
        )
        if pr is None:
            logger.warning("Cannot save review — PR %s#%s not in DB", full_name, pr_number)
            return None

        # Delete existing review (replace, don't duplicate)
        if pr.review:
            db.delete(pr.review)
            db.flush()

        issues = review_result.get("issues", [])
        critical = sum(1 for i in issues if i.get("severity") == "critical")
        high     = sum(1 for i in issues if i.get("severity") == "high")
        medium   = sum(1 for i in issues if i.get("severity") == "medium")
        low      = sum(1 for i in issues if i.get("severity") == "low")

        review = Review(
            pull_request_id=pr.id,
            classification=review_result.get("classification", "moderate"),
            approved=review_result.get("approved", False),
            plane_state=review_result.get("plane_state", "code_review"),
            summary=review_result.get("summary", "")[:2000],
            suggestion=review_result.get("suggestion", "")[:1000],
            total_issues=len(issues),
            critical_issues=critical,
            high_issues=high,
            medium_issues=medium,
            low_issues=low,
        )
        db.add(review)
        db.flush()  # get review.id before adding children

        for issue in issues:
            db.add(ReviewIssue(
                review_id=review.id,
                severity=issue.get("severity", "low"),
                file_path=issue.get("file", "")[:500],
                line_number=issue.get("line"),
                comment=issue.get("comment", "")[:2000],
            ))

        db.commit()
        db.refresh(review)
        logger.info(
            "Saved review for %s#%s: approved=%s issues=%d",
            full_name, pr_number, review.approved, len(issues),
        )
        return review
