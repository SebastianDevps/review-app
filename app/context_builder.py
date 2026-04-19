"""
Context builder — assembles the AI prompt context for a PR review.

Given a diff, this module:
  1. Extracts changed file paths from the diff header lines
  2. For each changed file, builds a semantic query (path + function names found in diff)
  3. Searches ChromaDB for the top-K most relevant chunks
  4. Deduplicates and ranks results
  5. Assembles a compact context block within the token budget

Token budget strategy (inspired by CodeRabbit's "1:1 code-to-context ratio"):
  - MAX_CONTEXT_CHARS = 6000 (~1500 tokens) for semantic context
  - Prioritize chunks from changed files first, then related files
  - Include CLAUDE.md if present (project rules always relevant)
  - Plane ticket if branch has issue ID

Result quality vs Phase 1:
  Phase 1: "este archivo tiene un posible null pointer en línea 42"
  Phase 2: "en WhatsappService.send_message() (línea 42), cuando
             last_message_at > 24h debes usar HSM template — esto viola
             la regla crítica definida en MessageRouter.route_outbound()
             (encontrado en app/routers/message_router.py:18)"
"""

import logging
import re
from pathlib import Path

from app.context_store import MAX_CONTEXT_CHARS, get_context_store

logger = logging.getLogger(__name__)

TOP_K_PER_FILE = 3     # chunks per changed file
TOP_K_GLOBAL = 5       # additional global semantic search
CLAUDE_MD_MAX = 1500   # max chars from CLAUDE.md


def extract_changed_files(diff: str) -> list[str]:
    """Extract list of changed file paths from a unified diff."""
    files = []
    for line in diff.splitlines():
        # Match: diff --git a/path/to/file.py b/path/to/file.py
        if line.startswith("diff --git"):
            match = re.search(r"b/(.+)$", line)
            if match:
                files.append(match.group(1))
    return list(dict.fromkeys(files))  # deduplicate, preserve order


def extract_symbols_from_diff(diff: str) -> list[str]:
    """
    Extract function/class names that appear in the diff context lines.
    These are used to enrich the semantic search query.
    """
    symbols = set()
    # Lines starting with @@ ... @@ def function_name / class ClassName
    for line in diff.splitlines():
        if line.startswith("@@"):
            # e.g. @@ -10,6 +10,8 @@ def send_message(self, phone, text):
            match = re.search(r"@@\s+(?:def|class|func|fn|function)\s+(\w+)", line)
            if match:
                symbols.add(match.group(1))

        # Also capture identifiers from added lines (+) that look like function calls
        if line.startswith("+") and not line.startswith("+++"):
            # Find potential function calls: identifier(
            for m in re.finditer(r"\b([a-zA-Z_]\w+)\s*\(", line):
                name = m.group(1)
                if len(name) > 3 and name not in ("if", "for", "while", "with", "return", "print"):
                    symbols.add(name)

    return list(symbols)[:10]  # cap at 10 symbols


def build_review_context(
    repo_full_name: str,
    diff: str,
    plane_context: str = "",
    repo_dir: str | None = None,
) -> str:
    """
    Build the complete context string to inject into the review prompt.

    Args:
        repo_full_name: e.g. 'zetainc-co/nellup'
        diff: unified diff of the PR
        plane_context: pre-fetched Plane ticket context string
        repo_dir: local path to repo (for CLAUDE.md reading)

    Returns:
        Formatted context string ready for injection into system prompt.
    """
    store = get_context_store()
    chunk_count = store.repo_chunk_count(repo_full_name)

    context_parts: list[str] = []

    # ── 1. Auto-generated project context (rich — generated post-indexing) ───────
    project_ctx = store.load_project_context(repo_full_name)
    if project_ctx:
        # Trim to budget share — project context is generous but not unlimited
        context_parts.append(f"## Project Context\n{project_ctx[:2500]}")
    else:
        # Fallback: bare CLAUDE.md if no generated context yet
        claude_md = _load_claude_md(repo_full_name, repo_dir)
        if claude_md:
            context_parts.append(f"## Project Rules (CLAUDE.md)\n{claude_md}")

    # ── 2. Plane ticket ────────────────────────────────────────────────────────
    if plane_context:
        context_parts.append(f"## Linked Ticket\n{plane_context}")

    # ── 3. Semantic context from ChromaDB ──────────────────────────────────────
    if chunk_count == 0:
        logger.info("No index for %s yet — using CLAUDE.md + ticket only", repo_full_name)
        return "\n\n".join(context_parts)

    changed_files = extract_changed_files(diff)
    symbols = extract_symbols_from_diff(diff)

    seen_chunk_ids: set[str] = set()
    semantic_blocks: list[str] = []
    total_chars = sum(len(p) for p in context_parts)

    # Per-file search: find chunks from each changed file
    for file_path in changed_files[:5]:  # cap at 5 files to avoid blowup
        if total_chars >= MAX_CONTEXT_CHARS:
            break

        # Build query: file path + any symbols found in that file's diff section
        query = f"file:{file_path}"
        if symbols:
            query += " functions: " + ", ".join(symbols[:5])

        results = store.search(repo_full_name, query, top_k=TOP_K_PER_FILE)
        for chunk in results:
            chunk_id = f"{chunk.get('file_path')}:{chunk.get('start_line')}"
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)

            block = _format_chunk(chunk)
            if total_chars + len(block) > MAX_CONTEXT_CHARS:
                break

            semantic_blocks.append(block)
            total_chars += len(block)

    # Global semantic search: find cross-file related code
    if symbols and total_chars < MAX_CONTEXT_CHARS:
        global_query = " ".join(symbols[:5])
        results = store.search(repo_full_name, global_query, top_k=TOP_K_GLOBAL)
        for chunk in results:
            chunk_id = f"{chunk.get('file_path')}:{chunk.get('start_line')}"
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)

            block = _format_chunk(chunk)
            if total_chars + len(block) > MAX_CONTEXT_CHARS:
                break

            semantic_blocks.append(block)
            total_chars += len(block)

    if semantic_blocks:
        context_parts.append(
            f"## Relevant Code Context ({len(semantic_blocks)} chunks from {len(changed_files)} changed files)\n"
            + "\n\n".join(semantic_blocks)
        )

    full_context = "\n\n".join(context_parts)
    logger.info(
        "Built context for %s: %d chars, %d semantic chunks, %d files",
        repo_full_name, len(full_context), len(semantic_blocks), len(changed_files),
    )
    return full_context


def _format_chunk(chunk: dict) -> str:
    """Format a single ChromaDB result chunk for prompt injection."""
    file_path = chunk.get("file_path", "?")
    name = chunk.get("name", "?")
    node_type = chunk.get("node_type", "")
    start_line = chunk.get("start_line", "?")
    end_line = chunk.get("end_line", "?")
    tags = chunk.get("tags", "")
    content = chunk.get("content_preview", "")

    tag_str = f" [{tags}]" if tags else ""
    header = f"### `{file_path}:{start_line}-{end_line}` — {node_type} `{name}`{tag_str}"
    return f"{header}\n```\n{content}\n```"


def _load_claude_md(repo_full_name: str, repo_dir: str | None) -> str:
    """Load CLAUDE.md from local repo snapshot."""
    # Try local repo dir first (set during indexing)
    if repo_dir:
        path = Path(repo_dir) / "CLAUDE.md"
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="ignore")
            return content[:CLAUDE_MD_MAX]

    # Try cached snapshot
    from app.repo_cloner import get_repo_dir
    cached = get_repo_dir(repo_full_name) / "CLAUDE.md"
    if cached.exists():
        content = cached.read_text(encoding="utf-8", errors="ignore")
        return content[:CLAUDE_MD_MAX]

    return ""
