"""
Tree-sitter based semantic code indexer.

Inspired by bobmatnyc/ai-code-review chunking architecture and GitNexus's
12-phase DAG (rebuilt from scratch — MIT clean).

What it does:
  - Walks a cloned repo directory
  - Parses each source file with Tree-sitter (16 languages)
  - Extracts semantic units: functions, classes, methods, routes, models
  - Returns a list of SemanticChunk objects ready for embedding

Why Tree-sitter over regex:
  - Understands language structure, not just text patterns
  - Extracts precise line ranges for each function/class
  - Cross-language: same interface for Python, JS, TS, Go, etc.
  - 95%+ token reduction vs sending whole files (bobmatnyc benchmark)
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Supported languages ────────────────────────────────────────────────────────
# Maps file extension → (tree-sitter language name, grammar package)
LANGUAGE_MAP: dict[str, tuple[str, str]] = {
    ".py": ("python", "tree-sitter-python"),
    ".js": ("javascript", "tree-sitter-javascript"),
    ".jsx": ("javascript", "tree-sitter-javascript"),
    ".ts": ("typescript", "tree-sitter-typescript"),
    ".tsx": ("tsx", "tree-sitter-typescript"),
    ".go": ("go", "tree-sitter-go"),
    ".rs": ("rust", "tree-sitter-rust"),
    ".java": ("java", "tree-sitter-java"),
    ".rb": ("ruby", "tree-sitter-ruby"),
    ".php": ("php", "tree-sitter-php"),
    ".cs": ("c_sharp", "tree-sitter-c-sharp"),
    ".cpp": ("cpp", "tree-sitter-cpp"),
    ".c": ("c", "tree-sitter-c"),
    ".kt": ("kotlin", "tree-sitter-kotlin"),
    ".swift": ("swift", "tree-sitter-swift"),
    ".scala": ("scala", "tree-sitter-scala"),
}

# Files/dirs to skip (mirrors GitNexus scan exclusions)
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "coverage", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "target", "vendor",
}

SKIP_EXTENSIONS = {
    ".lock", ".sum", ".mod", ".json", ".yaml", ".yml", ".toml",
    ".md", ".txt", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".zip",
}

# Node types that represent semantic units worth extracting
SEMANTIC_NODE_TYPES = {
    # Functions / methods
    "function_definition", "function_declaration", "method_definition",
    "arrow_function", "async_function", "function_item",  # Rust
    # Classes / structs
    "class_definition", "class_declaration", "struct_item", "impl_item",
    "interface_declaration", "abstract_class_declaration",
    # Routes / decorators (FastAPI, Express, etc.)
    "decorator", "call_expression",
    # Type definitions
    "type_alias_declaration", "interface_declaration", "enum_declaration",
}

MAX_CHUNK_CHARS = 2000   # max chars per semantic chunk sent to embedding model
MIN_CHUNK_LINES = 3      # ignore trivial 1-2 line snippets


@dataclass
class SemanticChunk:
    """A single semantic unit extracted from source code."""

    repo: str                          # e.g. "zetainc-co/nellup"
    file_path: str                     # relative path from repo root
    language: str                      # e.g. "python"
    node_type: str                     # e.g. "function_definition"
    name: str                          # function/class name if extractable
    start_line: int
    end_line: int
    content: str                       # the actual source text
    docstring: str = ""                # extracted docstring if present
    tags: list[str] = field(default_factory=list)   # e.g. ["route", "async", "auth"]
    # Context node fields (GitNexus-inspired call graph — static, no graph DB needed)
    calls: list[str] = field(default_factory=list)    # function names this chunk calls
    called_by: list[str] = field(default_factory=list)  # filled post-index via _resolve_call_graph

    @property
    def chunk_id(self) -> str:
        """Stable ID for deduplication in ChromaDB."""
        return f"{self.repo}:{self.file_path}:{self.start_line}"

    @property
    def summary(self) -> str:
        """Short summary for embedding — name + docstring + first line of content."""
        parts = [f"{self.node_type} {self.name}", f"in {self.file_path}"]
        if self.docstring:
            parts.append(self.docstring[:200])
        else:
            first_lines = self.content.splitlines()[:3]
            parts.append(" ".join(first_lines))
        return " | ".join(parts)


class RepoIndexer:
    """
    Indexes a local repo directory using Tree-sitter.

    Usage:
        indexer = RepoIndexer("zetainc-co/nellup")
        chunks = indexer.index("/tmp/repos/zetainc-co_nellup")
    """

    def __init__(self, repo_full_name: str):
        self.repo = repo_full_name
        self._parsers: dict[str, object] = {}

    def _get_parser(self, language: str):
        """Lazy-load Tree-sitter parser for a language."""
        if language in self._parsers:
            return self._parsers[language]

        try:
            import tree_sitter_languages
            parser = tree_sitter_languages.get_parser(language)
            self._parsers[language] = parser
            return parser
        except Exception:
            pass

        try:
            # Fallback: load via tree-sitter Language API
            import tree_sitter
            import importlib

            lang_module = importlib.import_module(f"tree_sitter_{language}")
            language_obj = tree_sitter.Language(lang_module.language())
            parser = tree_sitter.Parser(language_obj)
            self._parsers[language] = parser
            return parser
        except Exception as exc:
            logger.debug("No Tree-sitter parser for %s: %s", language, exc)
            return None

    def index(self, repo_dir: str) -> list[SemanticChunk]:
        """
        Walk the repo directory and extract semantic chunks.

        Args:
            repo_dir: Absolute path to the cloned repo.

        Returns:
            List of SemanticChunk objects.
        """
        repo_path = Path(repo_dir)
        if not repo_path.exists():
            raise FileNotFoundError(f"Repo directory not found: {repo_dir}")

        chunks: list[SemanticChunk] = []
        files_processed = 0
        files_skipped = 0

        for file_path in repo_path.rglob("*"):
            if not file_path.is_file():
                continue

            # Skip excluded dirs
            if any(skip in file_path.parts for skip in SKIP_DIRS):
                continue

            # Skip non-source files
            suffix = file_path.suffix.lower()
            if suffix in SKIP_EXTENSIONS or suffix not in LANGUAGE_MAP:
                files_skipped += 1
                continue

            language, _ = LANGUAGE_MAP[suffix]
            relative_path = str(file_path.relative_to(repo_path))

            try:
                file_chunks = self._parse_file(file_path, relative_path, language)
                chunks.extend(file_chunks)
                files_processed += 1
            except Exception as exc:
                logger.debug("Failed to parse %s: %s", relative_path, exc)
                files_skipped += 1

        # ── Context nodes: resolve call graph ────────────────────────────────
        _resolve_call_graph(chunks)

        logger.info(
            "Indexed %s: %d files parsed, %d skipped, %d chunks extracted",
            self.repo, files_processed, files_skipped, len(chunks),
        )
        return chunks

    def _parse_file(self, file_path: Path, relative_path: str, language: str) -> list[SemanticChunk]:
        """Parse a single file and extract semantic chunks."""
        try:
            source = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return []

        if not source.strip():
            return []

        parser = self._get_parser(language)
        if parser is None:
            # Fallback: no parser available — chunk by function-like patterns
            return self._fallback_chunk(source, relative_path, language)

        try:
            tree = parser.parse(source.encode("utf-8"))
        except Exception as exc:
            logger.debug("Tree-sitter parse error in %s: %s", relative_path, exc)
            return self._fallback_chunk(source, relative_path, language)

        lines = source.splitlines()
        chunks: list[SemanticChunk] = []
        self._walk_tree(tree.root_node, lines, relative_path, language, chunks)
        return chunks

    def _walk_tree(self, node, lines: list[str], file_path: str, language: str, chunks: list[SemanticChunk]) -> None:
        """Recursively walk the AST and collect semantic nodes."""
        if node.type in SEMANTIC_NODE_TYPES:
            chunk = self._node_to_chunk(node, lines, file_path, language)
            if chunk:
                chunks.append(chunk)
                return  # don't recurse into already-captured nodes

        for child in node.children:
            self._walk_tree(child, lines, file_path, language, chunks)

    def _node_to_chunk(self, node, lines: list[str], file_path: str, language: str) -> SemanticChunk | None:
        """Convert a Tree-sitter node to a SemanticChunk."""
        start_line = node.start_point[0]  # 0-indexed
        end_line = node.end_point[0]

        if (end_line - start_line) < MIN_CHUNK_LINES:
            return None

        content_lines = lines[start_line : end_line + 1]
        content = "\n".join(content_lines)

        # Cap at max chunk size
        if len(content) > MAX_CHUNK_CHARS:
            content = content[:MAX_CHUNK_CHARS] + "\n# ... (truncated)"

        name = self._extract_name(node)
        docstring = self._extract_docstring(node, lines, language)
        tags = self._infer_tags(node, content, language)
        calls = _extract_calls_from_content(content)

        return SemanticChunk(
            repo=self.repo,
            file_path=file_path,
            language=language,
            node_type=node.type,
            name=name,
            start_line=start_line + 1,   # 1-indexed for display
            end_line=end_line + 1,
            content=content,
            docstring=docstring,
            tags=tags,
            calls=calls,
        )

    def _extract_name(self, node) -> str:
        """Extract identifier name from a node."""
        # Most languages: first 'identifier' or 'name' child
        for child in node.children:
            if child.type in ("identifier", "name", "property_identifier", "type_identifier"):
                return child.text.decode("utf-8") if isinstance(child.text, bytes) else str(child.text)
        return node.type

    def _extract_docstring(self, node, lines: list[str], language: str) -> str:
        """Extract docstring / JSDoc comment above or inside the node."""
        start = node.start_point[0]

        # Look for comment line(s) immediately before the node
        if start > 0:
            prev_line = lines[start - 1].strip()
            if prev_line.startswith(("//", "#", "/*", "*", '"""', "'''")):
                return prev_line[:200]

        # Python: first string literal child
        if language == "python":
            for child in node.children:
                if child.type == "block":
                    for grandchild in child.children:
                        if grandchild.type == "expression_statement":
                            for ggc in grandchild.children:
                                if ggc.type == "string":
                                    text = ggc.text
                                    if isinstance(text, bytes):
                                        text = text.decode("utf-8")
                                    return text.strip('"\' ').splitlines()[0][:200]

        return ""

    def _infer_tags(self, node, content: str, language: str) -> list[str]:
        """Infer semantic tags from content patterns."""
        tags: list[str] = []
        content_lower = content.lower()

        # Framework-specific route detection
        if any(kw in content_lower for kw in ("@app.get", "@app.post", "@router.", "@get(", "@post(", "router.get", "router.post")):
            tags.append("route")
        if any(kw in content_lower for kw in ("async def", "async function", "asyncio")):
            tags.append("async")
        if any(kw in content_lower for kw in ("auth", "token", "jwt", "permission", "login", "oauth")):
            tags.append("auth")
        if any(kw in content_lower for kw in ("db.", "session.", "query(", "execute(", "cursor", "transaction")):
            tags.append("database")
        if any(kw in content_lower for kw in ("test_", "def test", "it(", "describe(")):
            tags.append("test")
        if any(kw in content_lower for kw in ("migration", "migrate", "alembic", "schema")):
            tags.append("migration")
        if any(kw in content_lower for kw in ("whatsapp", "hsm", "template", "twilio", "meta api")):
            tags.append("whatsapp")
        if any(kw in content_lower for kw in ("celery", "task", "delay(", "apply_async")):
            tags.append("queue")

        return tags

    def _fallback_chunk(self, source: str, relative_path: str, language: str) -> list[SemanticChunk]:
        """
        Fallback chunker when Tree-sitter parser is unavailable.
        Splits by blank lines into chunks of reasonable size.
        """
        chunks: list[SemanticChunk] = []
        lines = source.splitlines()
        current_block: list[str] = []
        start_line = 1

        for i, line in enumerate(lines, 1):
            current_block.append(line)
            if (not line.strip() or i == len(lines)) and len(current_block) >= MIN_CHUNK_LINES:
                content = "\n".join(current_block)
                if len(content) > 100:  # skip tiny empty blocks
                    chunks.append(SemanticChunk(
                        repo=self.repo,
                        file_path=relative_path,
                        language=language,
                        node_type="block",
                        name=f"block_L{start_line}",
                        start_line=start_line,
                        end_line=i,
                        content=content[:MAX_CHUNK_CHARS],
                    ))
                current_block = []
                start_line = i + 1

        return chunks


# ── Context nodes: call graph (GitNexus-inspired, static — no graph DB needed) ─

import re  # noqa: E402


def _extract_calls_from_content(content: str) -> list[str]:
    """
    Extract function/method names that this chunk calls.
    Finds: self.foo(, cls.bar(, await foo(, direct_call(
    """
    calls: set[str] = set()
    for m in re.finditer(r"(?:await\s+)?(?:self|cls)\.([a-zA-Z_]\w+)\s*\(", content):
        calls.add(m.group(1))
    for m in re.finditer(r"(?<![.\w])([a-z_][a-zA-Z_0-9]+)\s*\(", content):
        name = m.group(1)
        if name not in _PYTHON_BUILTINS and len(name) > 3:
            calls.add(name)
    return list(calls)[:20]


def _resolve_call_graph(chunks: list[SemanticChunk]) -> None:
    """
    Fill `called_by` on each chunk by inverting the `calls` lists.

    For every chunk A that calls "foo", find chunk B where B.name == "foo"
    and add A.name to B.called_by.

    GitNexus-style context nodes without a graph DB:
    each chunk knows its callers and callees after this runs.
    """
    name_to_chunks: dict[str, list[int]] = {}
    for i, chunk in enumerate(chunks):
        if chunk.name:
            name_to_chunks.setdefault(chunk.name, []).append(i)

    for chunk in chunks:
        for called_name in chunk.calls:
            for target_idx in name_to_chunks.get(called_name, []):
                target = chunks[target_idx]
                if chunk.name and chunk.name not in target.called_by:
                    target.called_by.append(chunk.name)


_PYTHON_BUILTINS = {
    "print", "len", "range", "str", "int", "float", "list", "dict", "set",
    "tuple", "bool", "type", "isinstance", "issubclass", "hasattr", "getattr",
    "setattr", "super", "open", "zip", "map", "filter", "sorted", "reversed",
    "enumerate", "iter", "next", "any", "all", "sum", "min", "max", "abs",
    "round", "repr", "format", "input", "vars", "dir", "id", "hash",
}
