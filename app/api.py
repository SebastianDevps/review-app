"""
Dashboard REST API — consumed by the Next.js frontend.

Endpoints:
  GET  /api/stats                    — workspace overview numbers
  GET  /api/repos                    — list repos with review stats
  GET  /api/repos/{owner}/{repo}/prs — PRs for a repo (paginated)
  GET  /api/reviews/{review_id}      — full review detail
  GET  /api/devs                     — per-developer metrics
  GET  /api/devs/{login}             — single developer detail
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Integer, desc, func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import PullRequest, Repository, Review, ReviewIssue

router = APIRouter(prefix="/api", tags=["dashboard"])


# ── Stats overview ─────────────────────────────────────────────────────────────

@router.get("/stats")
def get_stats(db: Session = Depends(get_db)) -> dict:
    """Overall workspace statistics for dashboard header cards."""
    total_repos = db.query(func.count(Repository.id)).filter(Repository.is_active == True).scalar() or 0
    total_prs = db.query(func.count(PullRequest.id)).scalar() or 0
    total_reviews = db.query(func.count(Review.id)).scalar() or 0

    approved = db.query(func.count(Review.id)).filter(Review.approved == True).scalar() or 0
    approval_rate = round((approved / total_reviews * 100) if total_reviews > 0 else 0, 1)

    # Last 7 days
    since = datetime.utcnow() - timedelta(days=7)
    prs_week = db.query(func.count(PullRequest.id)).filter(PullRequest.opened_at >= since).scalar() or 0

    # Total issues by severity
    critical = db.query(func.count(ReviewIssue.id)).filter(ReviewIssue.severity == "critical").scalar() or 0
    high = db.query(func.count(ReviewIssue.id)).filter(ReviewIssue.severity == "high").scalar() or 0

    return {
        "total_repos": total_repos,
        "total_prs": total_prs,
        "total_reviews": total_reviews,
        "approval_rate": approval_rate,
        "prs_last_7_days": prs_week,
        "total_critical_issues": critical,
        "total_high_issues": high,
    }


# ── Repositories ──────────────────────────────────────────────────────────────

@router.get("/repos")
def list_repos(db: Session = Depends(get_db)) -> list[dict]:
    """List all connected repos with their review stats."""
    from app.context_store import get_context_store
    store = get_context_store()

    repos = db.query(Repository).filter(Repository.is_active == True).order_by(desc(Repository.updated_at)).all()

    result = []
    for repo in repos:
        total_prs = db.query(func.count(PullRequest.id)).filter(PullRequest.repository_id == repo.id).scalar() or 0
        approved = (
            db.query(func.count(Review.id))
            .join(PullRequest)
            .filter(PullRequest.repository_id == repo.id, Review.approved == True)
            .scalar() or 0
        )
        approval_rate = round((approved / total_prs * 100) if total_prs > 0 else 0, 1)
        chunk_count = store.repo_chunk_count(repo.full_name)

        result.append({
            "id": repo.id,
            "full_name": repo.full_name,
            "owner": repo.owner,
            "name": repo.name,
            "default_branch": repo.default_branch,
            "indexed_at": repo.indexed_at.isoformat() if repo.indexed_at else None,
            "chunk_count": chunk_count,
            "index_status": "indexed" if chunk_count > 0 else "pending",
            "total_prs": total_prs,
            "approval_rate": approval_rate,
            "created_at": repo.created_at.isoformat(),
        })

    return result


@router.get("/repos/{owner}/{repo}/prs")
def list_repo_prs(
    owner: str,
    repo: str,
    page: int = 1,
    per_page: int = 20,
    db: Session = Depends(get_db),
) -> dict:
    """List pull requests for a specific repo, with their review results."""
    full_name = f"{owner}/{repo}"
    repository = db.query(Repository).filter(Repository.full_name == full_name).first()
    if not repository:
        raise HTTPException(status_code=404, detail=f"Repo {full_name} not found")

    offset = (page - 1) * per_page
    total = db.query(func.count(PullRequest.id)).filter(PullRequest.repository_id == repository.id).scalar() or 0

    prs = (
        db.query(PullRequest)
        .options(joinedload(PullRequest.review))
        .filter(PullRequest.repository_id == repository.id)
        .order_by(desc(PullRequest.opened_at))
        .offset(offset)
        .limit(per_page)
        .all()
    )

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "items": [_pr_to_dict(pr) for pr in prs],
    }


# ── Reviews ───────────────────────────────────────────────────────────────────

@router.get("/reviews/{review_id}")
def get_review(review_id: int, db: Session = Depends(get_db)) -> dict:
    """Full review detail including all issues."""
    review = (
        db.query(Review)
        .options(
            joinedload(Review.issues),
            joinedload(Review.pull_request).joinedload(PullRequest.repository),
        )
        .filter(Review.id == review_id)
        .first()
    )
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    pr = review.pull_request
    return {
        "id": review.id,
        "classification": review.classification,
        "approved": review.approved,
        "plane_state": review.plane_state,
        "summary": review.summary,
        "suggestion": review.suggestion,
        "reviewed_at": review.reviewed_at.isoformat(),
        "total_issues": review.total_issues,
        "issues_by_severity": {
            "critical": review.critical_issues,
            "high": review.high_issues,
            "medium": review.medium_issues,
            "low": review.low_issues,
        },
        "issues": [
            {
                "severity": i.severity,
                "file": i.file_path,
                "line": i.line_number,
                "comment": i.comment,
            }
            for i in sorted(review.issues, key=lambda x: ["critical", "high", "medium", "low"].index(x.severity))
        ],
        "pull_request": {
            "number": pr.pr_number,
            "title": pr.title,
            "author": pr.author,
            "branch": pr.head_branch,
            "plane_issue_id": pr.plane_issue_id,
            "additions": pr.additions,
            "deletions": pr.deletions,
            "repo": pr.repository.full_name,
        },
    }


# ── Developer metrics ─────────────────────────────────────────────────────────

@router.get("/devs")
def list_devs(db: Session = Depends(get_db)) -> list[dict]:
    """Per-developer metrics across all repos."""
    # Aggregate stats per author
    rows = (
        db.query(
            PullRequest.author,
            func.count(PullRequest.id).label("total_prs"),
            func.count(Review.id).label("reviewed_prs"),
            func.sum(func.cast(Review.approved, Integer)).label("approved_prs"),
            func.sum(Review.total_issues).label("total_issues"),
            func.sum(Review.critical_issues).label("critical_issues"),
            func.max(PullRequest.opened_at).label("last_pr_at"),
        )
        .outerjoin(Review, Review.pull_request_id == PullRequest.id)
        .group_by(PullRequest.author)
        .order_by(desc(func.count(PullRequest.id)))
        .all()
    )

    result = []
    for row in rows:
        reviewed = row.reviewed_prs or 0
        approved = int(row.approved_prs or 0)
        approval_rate = round((approved / reviewed * 100) if reviewed > 0 else 0, 1)

        result.append({
            "author": row.author,
            "total_prs": row.total_prs,
            "reviewed_prs": reviewed,
            "approved_prs": approved,
            "approval_rate": approval_rate,
            "total_issues": int(row.total_issues or 0),
            "critical_issues": int(row.critical_issues or 0),
            "last_pr_at": row.last_pr_at.isoformat() if row.last_pr_at else None,
        })

    return result


@router.get("/devs/{login}")
def get_dev(login: str, db: Session = Depends(get_db)) -> dict:
    """Single developer's PRs and review history."""
    prs = (
        db.query(PullRequest)
        .options(
            joinedload(PullRequest.review).joinedload(Review.issues),
            joinedload(PullRequest.repository),
        )
        .filter(PullRequest.author == login)
        .order_by(desc(PullRequest.opened_at))
        .limit(50)
        .all()
    )

    if not prs:
        raise HTTPException(status_code=404, detail=f"Developer {login} not found")

    reviews_done = [pr for pr in prs if pr.review]
    approved = [pr for pr in reviews_done if pr.review.approved]

    # Most frequent issue types
    all_issues = [issue for pr in reviews_done for issue in pr.review.issues]
    issue_freq: dict[str, int] = {}
    for issue in all_issues:
        key = issue.file_path.split("/")[-1] if issue.file_path else "unknown"
        issue_freq[key] = issue_freq.get(key, 0) + 1
    top_files = sorted(issue_freq.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "author": login,
        "stats": {
            "total_prs": len(prs),
            "reviewed_prs": len(reviews_done),
            "approval_rate": round(len(approved) / len(reviews_done) * 100 if reviews_done else 0, 1),
            "total_issues": len(all_issues),
            "critical_issues": sum(1 for i in all_issues if i.severity == "critical"),
        },
        "top_issue_files": [{"file": f, "count": c} for f, c in top_files],
        "recent_prs": [_pr_to_dict(pr) for pr in prs[:20]],
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pr_to_dict(pr: PullRequest) -> dict:
    review = pr.review
    return {
        "id": pr.id,
        "number": pr.pr_number,
        "title": pr.title,
        "author": pr.author,
        "branch": pr.head_branch,
        "plane_issue_id": pr.plane_issue_id,
        "additions": pr.additions,
        "deletions": pr.deletions,
        "opened_at": pr.opened_at.isoformat(),
        "review": {
            "id": review.id,
            "classification": review.classification,
            "approved": review.approved,
            "plane_state": review.plane_state,
            "total_issues": review.total_issues,
            "critical_issues": review.critical_issues,
            "reviewed_at": review.reviewed_at.isoformat(),
        } if review else None,
    }

