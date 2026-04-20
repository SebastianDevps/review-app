"""
Unit tests for ReviewEngine — mock mode only, zero external calls.
"""

import pytest
from app.review_engine import ReviewEngine


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    """ReviewEngine in MOCK_AI mode (MOCK_AI=true set in conftest)."""
    return ReviewEngine(config={
        "thresholds": {"trivial_max_lines": 50},
        "review": {"blocking_severities": ["critical", "high"]},
    })


SMALL_DIFF = """\
diff --git a/app/utils.py b/app/utils.py
index abc..def 100644
--- a/app/utils.py
+++ b/app/utils.py
@@ -1,3 +1,5 @@
 def helper():
-    pass
+    return 42
"""

LARGE_DIFF = "\n".join(
    [f"diff --git a/file{i}.py b/file{i}.py\n+    # change {i}" for i in range(100)]
)

PR_META_SMALL = {"additions": 2, "deletions": 1, "changed_files": 1, "title": "tiny fix"}
PR_META_LARGE = {"additions": 200, "deletions": 50, "changed_files": 8, "title": "big refactor"}


# ── Classification logic ───────────────────────────────────────────────────────

def test_trivial_pr_auto_approved(engine):
    result = engine.review(SMALL_DIFF, PR_META_SMALL)
    assert result["classification"] == "trivial"
    assert result["approved"] is True
    assert result["issues"] == []
    assert result["plane_state"] == "qa_testing"


def test_large_pr_returns_mock_review(engine):
    result = engine.review(LARGE_DIFF, PR_META_LARGE)
    assert result["classification"] in ("moderate", "complex")
    assert "approved" in result
    assert isinstance(result["issues"], list)
    assert len(result["issues"]) > 0


def test_mock_review_has_required_fields(engine):
    result = engine.review(LARGE_DIFF, PR_META_LARGE)
    for field in ("classification", "approved", "issues", "plane_state", "pr_comment", "plane_comment"):
        assert field in result, f"Missing field: {field}"


def test_mock_review_issues_have_severity(engine):
    result = engine.review(LARGE_DIFF, PR_META_LARGE)
    for issue in result["issues"]:
        assert issue["severity"] in ("critical", "high", "medium", "low")
        assert "file" in issue
        assert "comment" in issue


def test_pr_comment_contains_summary(engine):
    result = engine.review(LARGE_DIFF, PR_META_LARGE)
    assert "AI Code Review" in result["pr_comment"]
    assert "MOCK" in result["pr_comment"]


def test_plane_comment_contains_state(engine):
    result = engine.review(LARGE_DIFF, PR_META_LARGE)
    assert "AI Code Review" in result["plane_comment"]
    assert any(s in result["plane_comment"] for s in ("✅", "❌", "⏳"))


def test_classification_trivial_with_small_stats(engine):
    """Small diff stats → trivial via _classify mock heuristic."""
    tiny_diff = "diff --git a/a.txt b/a.txt\n+# comment"
    result = engine._classify(tiny_diff)
    assert result == "trivial"


def test_classification_complex_with_many_lines(engine):
    stats = "\n".join([f"@@ -{i},1 +{i},2 @@" for i in range(35)])
    result = engine._classify(stats)
    assert result == "complex"
