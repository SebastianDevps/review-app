"""
AI Review Engine — Haiku classifies, Sonnet reviews with semantic context.

Phase 2 change: context is now built externally by context_builder.py
(CLAUDE.md + Plane ticket + semantic chunks from ChromaDB).
The engine receives a fully assembled context string and focuses on review logic.

Token strategy:
  - Haiku classifies on diff stats only (~350 tokens)
  - Trivial PRs: auto-approved, no Sonnet call
  - Moderate/Complex: Sonnet reviews with full context, diff chunked at 12K chars
  - System prompt identical across chunks → Anthropic prompt caching applies
  - Context capped at MAX_CONTEXT_CHARS to prevent blowup

Quality improvement from Phase 1 → Phase 2:
  Phase 1: "null pointer possible at line 42"
  Phase 2: "send_message() at line 42 calls WhatsappClient.send() without
             checking last_message_at against 24h window — this violates
             the HSM-template rule defined in MessageRouter.route_outbound()
             (app/routers/message_router.py:18). Use send_hsm_template()
             for contacts whose window has expired."
"""

import json
import logging
import re

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

CLASSIFY_MODEL = "claude-haiku-4-5-20251001"
REVIEW_MODEL = "claude-sonnet-4-6"
CHUNK_SIZE = 12_000   # chars per diff chunk


class ReviewEngine:
    def __init__(self, config: dict):
        self.config = config
        self._mock = settings.mock_ai
        if not self._mock:
            self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def _classify(self, diff_stats: str) -> str:
        """Use Haiku to classify PR complexity on diff stats only."""
        if self._mock:
            lines = diff_stats.count("\n")
            return "trivial" if lines < 10 else "moderate" if lines < 30 else "complex"
        response = self.client.messages.create(
            model=CLASSIFY_MODEL,
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": (
                    "Classify as exactly one word: trivial, moderate, or complex.\n"
                    "trivial = docs/config/lock files, < 50 lines\n"
                    "moderate = 50-300 lines, single concern\n"
                    "complex = >300 lines, multiple concerns, auth/payments/migrations/AI logic\n\n"
                    f"Diff stats:\n{diff_stats}\n\nOne word:"
                ),
            }],
        )
        raw = response.content[0].text.strip().lower()
        classification = raw if raw in ("trivial", "moderate", "complex") else "moderate"
        logger.info("Classification: %s (%d input tokens)", classification, response.usage.input_tokens)
        return classification

    def _build_system_prompt(self, context: str) -> str:
        """
        Build the system prompt with pre-assembled context.
        This prompt is identical across synchronize events → Anthropic caches it.
        """
        prompt = (
            "You are a senior code reviewer. Identify bugs, security issues, "
            "and violations of project-specific rules.\n\n"
            "Focus on: correctness, security, and project rule violations. "
            "Skip style unless it is a defined project rule.\n\n"
        )

        if context:
            prompt += f"{context}\n\n"

        prompt += (
            "Respond ONLY with valid JSON — no markdown fences, no extra text:\n"
            "{\n"
            '  "summary": "2-3 sentence overview of what changed and main findings",\n'
            '  "approved": true or false,\n'
            '  "issues": [\n'
            '    {\n'
            '      "severity": "critical|high|medium|low",\n'
            '      "file": "path/to/file.py",\n'
            '      "line": 42,\n'
            '      "comment": "clear explanation referencing specific functions/lines from context"\n'
            '    }\n'
            "  ],\n"
            '  "plane_state": "qa_testing|code_review|refused",\n'
            '  "suggestion": "actionable overall suggestion for the PR author"\n'
            "}\n\n"
            "plane_state rules:\n"
            "  qa_testing  = approved, no critical/high issues\n"
            "  refused     = has critical or high issues\n"
            "  code_review = has only medium/low issues, needs minor fixes"
        )
        return prompt

    def _review_chunk(self, system_prompt: str, chunk: str, chunk_num: int, total: int) -> dict:
        """Send one diff chunk to Sonnet. Returns parsed JSON result."""
        response = self.client.messages.create(
            model=REVIEW_MODEL,
            max_tokens=2000,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": (
                    f"Review this diff (chunk {chunk_num}/{total}):\n\n"
                    f"```diff\n{chunk}\n```"
                ),
            }],
        )
        logger.info(
            "Sonnet chunk %d/%d: %d in / %d out tokens",
            chunk_num, total, response.usage.input_tokens, response.usage.output_tokens,
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Could not parse Sonnet JSON, using raw summary")
            return {
                "summary": raw[:500],
                "approved": False,
                "issues": [],
                "plane_state": "code_review",
                "suggestion": "",
            }

    def review(
        self,
        diff: str,
        pr_metadata: dict,
        context: str = "",         # Phase 2: pre-built by context_builder.py
    ) -> dict:
        """
        Full review pipeline.

        Args:
            diff: unified diff of the PR
            pr_metadata: dict with title, author, additions, deletions, etc.
            context: assembled context string (CLAUDE.md + Plane ticket + semantic chunks)

        Returns:
            dict with classification, approved, issues, plane_state,
            pr_comment, plane_comment
        """
        # ── 1. Classify ───────────────────────────────────────────────────────
        stat_lines = [l for l in diff.split("\n") if l.startswith("diff --git") or l.startswith("@@")]
        diff_stats = "\n".join(stat_lines[:50])
        classification = self._classify(diff_stats)

        trivial_max = self.config.get("thresholds", {}).get("trivial_max_lines", 50)
        total_changes = pr_metadata.get("additions", 0) + pr_metadata.get("deletions", 0)

        if classification == "trivial" or total_changes <= trivial_max:
            logger.info("Trivial PR (%d lines) — skipping Sonnet", total_changes)
            return {
                "classification": "trivial",
                "approved": True,
                "issues": [],
                "plane_state": "qa_testing",
                "pr_comment": self._format_trivial_comment(pr_metadata),
                "plane_comment": "✅ PR clasificado como **trivial** — moviendo a QA/Testing.",
            }

        # ── 2. Sonnet review (chunked diff) ───────────────────────────────────
        if self._mock:
            return self._mock_review(diff, pr_metadata, classification)

        system_prompt = self._build_system_prompt(context)
        chunks = [diff[i : i + CHUNK_SIZE] for i in range(0, len(diff), CHUNK_SIZE)]
        all_issues: list[dict] = []
        base_result: dict = {}

        for i, chunk in enumerate(chunks):
            chunk_result = self._review_chunk(system_prompt, chunk, i + 1, len(chunks))
            all_issues.extend(chunk_result.get("issues", []))
            if not base_result:
                base_result = chunk_result

        base_result["issues"] = all_issues
        base_result["classification"] = classification

        # Determine final state from issues
        blocking = self.config.get("review", {}).get("blocking_severities", ["critical", "high"])
        blocking_count = sum(1 for iss in all_issues if iss.get("severity") in blocking)

        if blocking_count > 0:
            base_result["approved"] = False
            base_result["plane_state"] = "refused"
        elif not base_result.get("approved", True):
            base_result["plane_state"] = "code_review"
        else:
            base_result["plane_state"] = "qa_testing"

        # ── 3. Format output comments ─────────────────────────────────────────
        base_result["pr_comment"] = self._format_pr_comment(base_result, pr_metadata)
        base_result["plane_comment"] = self._format_plane_comment(base_result)

        return base_result

    # ── Comment formatters ────────────────────────────────────────────────────

    def _format_pr_comment(self, result: dict, pr_metadata: dict) -> str:
        classification = result.get("classification", "?")
        approved = result.get("approved", False)
        summary = result.get("summary", "No summary available.")
        issues = result.get("issues", [])
        suggestion = result.get("suggestion", "")

        status = "✅ Approved" if approved else "❌ Changes Requested"
        body = f"## 🤖 AI Code Review — `{classification}` — {status}\n\n"
        body += f"**Summary:** {summary}\n\n"

        if issues:
            critical = [i for i in issues if i.get("severity") in ("critical", "high")]
            medium_low = [i for i in issues if i.get("severity") in ("medium", "low")]

            if critical:
                body += f"### 🔴 Critical / High ({len(critical)})\n"
                for issue in critical[:8]:
                    body += (
                        f"- **`{issue.get('file','?')}:{issue.get('line','?')}`** "
                        f"[{issue.get('severity','?')}] — {issue.get('comment','')}\n"
                    )
                body += "\n"

            if medium_low:
                body += f"### 🟡 Medium / Low ({len(medium_low)})\n"
                for issue in medium_low[:5]:
                    body += (
                        f"- `{issue.get('file','?')}:{issue.get('line','?')}` "
                        f"— {issue.get('comment','')}\n"
                    )
                body += "\n"
        else:
            body += "No issues found. 🎉\n\n"

        if suggestion:
            body += f"**Suggestion:** {suggestion}\n\n"

        body += "---\n*🤖 [Review App](https://github.com/zetainc-co/review-app) — AI-powered, Plane-integrated*"
        return body

    def _format_plane_comment(self, result: dict) -> str:
        approved = result.get("approved", False)
        issues = result.get("issues", [])
        blocking = [i for i in issues if i.get("severity") in ("critical", "high")]
        state = result.get("plane_state", "code_review")

        state_map = {
            "qa_testing": "✅ Moviendo a **QA/Testing**",
            "refused": "❌ Moviendo a **Refused**",
            "code_review": "⏳ Permanece en **Code Review** (correcciones menores)",
        }

        lines = [
            "### 🤖 AI Code Review completado",
            f"**Resultado:** {'✅ Aprobado' if approved else '❌ Requiere cambios'}",
            f"**Issues:** {len(issues)} total — {len(blocking)} críticos/altos",
            state_map.get(state, state),
        ]

        if blocking:
            lines.append("\n**Issues bloqueantes:**")
            for issue in blocking[:4]:
                lines.append(
                    f"- `{issue.get('file','?')}:{issue.get('line','?')}` — {issue.get('comment','')}"
                )

        return "\n".join(lines)

    def _mock_review(self, diff: str, pr_metadata: dict, classification: str) -> dict:
        """Return a realistic-looking mock review for local testing without Anthropic API."""
        lines_changed = pr_metadata.get("additions", 0) + pr_metadata.get("deletions", 0)
        mock_issues = [
            {
                "severity": "medium",
                "file": "app/example.py",
                "line": 42,
                "comment": "[MOCK] Consider adding input validation here before processing.",
            },
            {
                "severity": "low",
                "file": "app/example.py",
                "line": 10,
                "comment": "[MOCK] Missing docstring on public function.",
            },
        ]
        result = {
            "classification": classification,
            "approved": True,
            "issues": mock_issues,
            "plane_state": "qa_testing",
            "summary": (
                f"[MOCK REVIEW] PR modifies {pr_metadata.get('changed_files', '?')} files "
                f"with {lines_changed} line changes. No critical issues found in mock mode."
            ),
            "suggestion": "[MOCK] Add tests for the new functionality.",
        }
        result["pr_comment"] = self._format_pr_comment(result, pr_metadata)
        result["plane_comment"] = self._format_plane_comment(result)
        return result

    def _format_trivial_comment(self, pr_metadata: dict) -> str:
        changes = pr_metadata.get("additions", 0) + pr_metadata.get("deletions", 0)
        return (
            f"## ✅ AI Code Review — `trivial`\n\n"
            f"PR with {changes} line changes — classified as trivial (docs/config/minor). "
            f"Moving directly to QA/Testing.\n\n"
            "---\n*🤖 [Review App](https://github.com/zetainc-co/review-app)*"
        )
