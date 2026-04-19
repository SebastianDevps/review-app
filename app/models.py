"""
SQLAlchemy ORM models — stores review history for dashboard.

Tables:
  - repositories   : connected repos with index metadata
  - pull_requests  : PR events received from GitHub
  - reviews        : AI review results per PR
  - review_issues  : individual issues found in a review
"""

import datetime
from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer,
    String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    owner: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(100), default="main")
    indexed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    pull_requests: Mapped[list["PullRequest"]] = relationship(back_populates="repository", cascade="all, delete-orphan")


class PullRequest(Base):
    __tablename__ = "pull_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), nullable=False, index=True)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), default="")
    author: Mapped[str] = mapped_column(String(100), nullable=False)
    head_branch: Mapped[str] = mapped_column(String(200), default="")
    base_branch: Mapped[str] = mapped_column(String(200), default="main")
    plane_issue_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    changed_files: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    merged_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    repository: Mapped["Repository"] = relationship(back_populates="pull_requests")
    review: Mapped["Review | None"] = relationship(back_populates="pull_request", uselist=False, cascade="all, delete-orphan")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pull_request_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id"), nullable=False, unique=True)
    classification: Mapped[str] = mapped_column(String(20), nullable=False)  # trivial/moderate/complex
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    plane_state: Mapped[str] = mapped_column(String(50), default="code_review")
    summary: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    total_issues: Mapped[int] = mapped_column(Integer, default=0)
    critical_issues: Mapped[int] = mapped_column(Integer, default=0)
    high_issues: Mapped[int] = mapped_column(Integer, default=0)
    medium_issues: Mapped[int] = mapped_column(Integer, default=0)
    low_issues: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    pull_request: Mapped["PullRequest"] = relationship(back_populates="review")
    issues: Mapped[list["ReviewIssue"]] = relationship(back_populates="review", cascade="all, delete-orphan")


class ReviewIssue(Base):
    __tablename__ = "review_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id"), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # critical/high/medium/low
    file_path: Mapped[str] = mapped_column(String(500), default="")
    line_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")

    review: Mapped["Review"] = relationship(back_populates="issues")
