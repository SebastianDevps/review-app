"""
Project context generator — server-side equivalent of `mempalace export`.

After indexing a repo with Tree-sitter, generates a rich project context
document from the semantic chunks. This is stored once per repo and injected
into every review prompt, giving the AI persistent project knowledge.

MemPalace insight adapted for server-side:
  - MemPalace: developer runs `mempalace mine` locally, exports CLAUDE.md, commits it
  - Our approach: generate equivalent context automatically from the Tree-sitter index
    post-indexing, store in ChromaDB's companion store, inject into every review

What gets generated:
  1. Architecture overview — top-level directories, dominant languages
  2. Key entry points — routes, main files, app entrypoints
  3. Core domain modules — classes and functions with high tag density
  4. Data models — ORM/schema classes
  5. Critical rules — patterns from existing CLAUDE.md if present
  6. Test coverage map — test files and what they cover
"""

import logging
from collections import Counter, defaultdict
from pathlib import Path

from app.indexer import SemanticChunk

logger = logging.getLogger(__name__)

MAX_CONTEXT_MD_CHARS = 8000  # generous limit — this is stored, not sent per-token


def generate_project_context(
    repo_full_name: str,
    chunks: list[SemanticChunk],
    repo_dir: str,
) -> str:
    """
    Generate a structured project context markdown from indexed chunks.

    Args:
        repo_full_name: e.g. 'acme/backend'
        chunks: all SemanticChunks produced by RepoIndexer
        repo_dir: local path to cloned repo snapshot

    Returns:
        Markdown string ready to be stored and injected into review prompts.
    """
    repo_path = Path(repo_dir)
    sections: list[str] = []

    sections.append(f"# Project Context: {repo_full_name}\n")
    sections.append(f"_Auto-generated from {len(chunks)} semantic chunks (Tree-sitter index)_\n")

    # ── 1. Architecture overview ─────────────────────────────────────────────────
    arch_section = _architecture_overview(chunks)
    if arch_section:
        sections.append(arch_section)

    # ── 2. Entry points (routes, main, app) ──────────────────────────────────────
    routes_section = _entry_points(chunks)
    if routes_section:
        sections.append(routes_section)

    # ── 3. Core domain modules ────────────────────────────────────────────────────
    domain_section = _core_domain(chunks)
    if domain_section:
        sections.append(domain_section)

    # ── 4. Data models ────────────────────────────────────────────────────────────
    models_section = _data_models(chunks)
    if models_section:
        sections.append(models_section)

    # ── 5. CLAUDE.md (developer-authored rules — highest priority) ────────────────
    claude_md = _read_claude_md(repo_path)
    if claude_md:
        sections.append(f"## Developer Rules (CLAUDE.md)\n\n{claude_md}")

    # ── 6. Test coverage map ──────────────────────────────────────────────────────
    test_section = _test_coverage(chunks)
    if test_section:
        sections.append(test_section)

    result = "\n\n".join(sections)

    # Truncate gracefully if over budget
    if len(result) > MAX_CONTEXT_MD_CHARS:
        result = result[:MAX_CONTEXT_MD_CHARS] + "\n\n_[context truncated]_"

    logger.info(
        "Generated project context for %s: %d chars from %d chunks",
        repo_full_name, len(result), len(chunks),
    )
    return result


# ── Section builders ──────────────────────────────────────────────────────────────

def _architecture_overview(chunks: list[SemanticChunk]) -> str:
    """Summarize languages, top directories, and chunk distribution."""
    lang_counts: Counter = Counter()
    dir_counts: Counter = Counter()

    for chunk in chunks:
        if chunk.language:
            lang_counts[chunk.language] += 1
        # Top-level directory
        parts = Path(chunk.file_path).parts
        if len(parts) >= 2:
            dir_counts[parts[0]] += 1

    lines = ["## Architecture Overview\n"]

    # Languages
    if lang_counts:
        top_langs = lang_counts.most_common(6)
        lang_str = ", ".join(f"{lang} ({n})" for lang, n in top_langs)
        lines.append(f"**Languages:** {lang_str}")

    # Top directories
    if dir_counts:
        top_dirs = dir_counts.most_common(8)
        dir_str = ", ".join(f"`{d}/`" for d, _ in top_dirs)
        lines.append(f"**Top directories:** {dir_str}")

    # Node type distribution
    type_counts: Counter = Counter(c.node_type for c in chunks if c.node_type)
    if type_counts:
        top_types = type_counts.most_common(5)
        type_str = ", ".join(f"{t} ({n})" for t, n in top_types)
        lines.append(f"**Semantic units:** {type_str}")

    return "\n".join(lines)


def _entry_points(chunks: list[SemanticChunk]) -> str:
    """Extract API routes, main entrypoints, and application setup."""
    route_chunks = [c for c in chunks if "route" in c.tags]
    async_chunks = [c for c in chunks if "async" in c.tags and c.node_type in ("function_definition", "method_definition")]

    if not route_chunks and not async_chunks:
        return ""

    lines = ["## Entry Points & Routes\n"]

    if route_chunks:
        lines.append(f"**API Routes ({len(route_chunks)} found):**")
        # Group by file
        by_file: dict[str, list[SemanticChunk]] = defaultdict(list)
        for c in route_chunks[:20]:
            by_file[c.file_path].append(c)
        for file_path, file_chunks in list(by_file.items())[:6]:
            names = [c.name for c in file_chunks if c.name]
            if names:
                lines.append(f"- `{file_path}`: {', '.join(f'`{n}`' for n in names[:6])}")

    if async_chunks and len(lines) < 15:
        lines.append(f"\n**Async handlers ({min(len(async_chunks), 10)} shown):**")
        seen_files: set[str] = set()
        for c in async_chunks[:10]:
            if c.file_path not in seen_files:
                seen_files.add(c.file_path)
                lines.append(f"- `{c.file_path}:{c.start_line}` — `{c.name}`")

    return "\n".join(lines)


def _core_domain(chunks: list[SemanticChunk]) -> str:
    """
    Identify core domain modules: files with the most diverse semantic chunk types
    (high signal = actual domain logic, not just utility files).
    """
    # Score files: unique node types + tag diversity
    file_scores: Counter = Counter()
    file_chunks: dict[str, list[SemanticChunk]] = defaultdict(list)

    for chunk in chunks:
        file_chunks[chunk.file_path].append(chunk)

    for file_path, file_chunk_list in file_chunks.items():
        # Skip test files and migrations for this section
        if any(t in file_path for t in ("test_", "_test.", "migration", "/tests/")):
            continue
        # Score: unique node types + number of chunks + tag diversity
        unique_types = len({c.node_type for c in file_chunk_list})
        unique_tags = len({t for c in file_chunk_list for t in c.tags})
        score = unique_types * 2 + len(file_chunk_list) + unique_tags
        file_scores[file_path] = score

    if not file_scores:
        return ""

    top_files = file_scores.most_common(10)
    lines = ["## Core Domain Modules\n"]
    lines.append("_Files with highest semantic density (key business logic):_\n")

    for file_path, _ in top_files:
        file_chunk_list = file_chunks[file_path]
        # Collect names grouped by type
        by_type: dict[str, list[str]] = defaultdict(list)
        for c in file_chunk_list:
            if c.name:
                by_type[c.node_type].append(c.name)

        summary_parts = []
        for node_type, names in sorted(by_type.items()):
            type_label = node_type.replace("_definition", "").replace("_", " ")
            shown = names[:4]
            summary_parts.append(f"{type_label}: {', '.join(f'`{n}`' for n in shown)}")

        if summary_parts:
            lines.append(f"- **`{file_path}`**")
            for part in summary_parts[:3]:
                lines.append(f"  - {part}")

    return "\n".join(lines)


def _data_models(chunks: list[SemanticChunk]) -> str:
    """Extract ORM models, schema classes, and data structures."""
    model_chunks = [
        c for c in chunks
        if "database" in c.tags
        or c.node_type in ("class_definition",)
        and any(kw in c.summary.lower() for kw in ("model", "schema", "entity", "table", "orm"))
    ]

    # Also grab classes named *Model, *Schema, *Entity
    model_chunks += [
        c for c in chunks
        if c.node_type == "class_definition"
        and c.name
        and any(c.name.endswith(suffix) for suffix in ("Model", "Schema", "Entity", "Table", "Base"))
        and c not in model_chunks
    ]

    if not model_chunks:
        return ""

    lines = ["## Data Models\n"]

    by_file: dict[str, list[SemanticChunk]] = defaultdict(list)
    for c in model_chunks[:20]:
        by_file[c.file_path].append(c)

    for file_path, file_chunks_list in list(by_file.items())[:8]:
        names = [c.name for c in file_chunks_list if c.name]
        if names:
            lines.append(f"- `{file_path}`: {', '.join(f'`{n}`' for n in names[:6])}")

    return "\n".join(lines)


def _test_coverage(chunks: list[SemanticChunk]) -> str:
    """Summarize test files and what they appear to cover."""
    test_chunks = [c for c in chunks if "test" in c.tags]

    if not test_chunks:
        return ""

    lines = ["## Test Coverage\n"]
    lines.append(f"**{len(test_chunks)} test functions** found\n")

    # Group test files
    test_files: set[str] = {c.file_path for c in test_chunks}
    for tf in sorted(test_files)[:8]:
        file_test_chunks = [c for c in test_chunks if c.file_path == tf]
        names = [c.name for c in file_test_chunks if c.name][:5]
        lines.append(f"- `{tf}`: {', '.join(f'`{n}`' for n in names)}")

    return "\n".join(lines)


def _read_claude_md(repo_path: Path) -> str:
    """Read CLAUDE.md from the repo root if present. Max 2000 chars."""
    claude_path = repo_path / "CLAUDE.md"
    if claude_path.exists():
        try:
            content = claude_path.read_text(encoding="utf-8", errors="ignore")
            return content[:2000]
        except Exception:
            pass
    return ""
