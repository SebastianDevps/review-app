"""
ChromaDB context store + BM25 hybrid search.

Architecture (GitNexus pattern, rebuilt MIT-clean):
  - One ChromaDB collection per repo for vector search (cosine similarity)
  - One BM25 index per repo for exact/keyword search (rank_bm25)
  - Hybrid retrieval via Reciprocal Rank Fusion (RRF):
      final_score = Σ 1/(k + rank_i)  where k=60 (standard RRF constant)
  - BM25 index persisted as JSON alongside ChromaDB to survive restarts

Why hybrid > vector-only:
  - Vector: finds semantically related code ("auth logic", "message sending")
  - BM25:   finds exact identifiers ("send_hsm_template", "is_blacklisted")
  - RRF:    combines both without needing score normalization
  - GitNexus benchmark: hybrid recall@5 = 96.6% vs vector-only 89.2%

Why RRF over weighted sum:
  - No need to tune α weight per query type
  - Rank-based: immune to score scale differences between BM25 and cosine
  - Standard production pattern (used by Elasticsearch, Vespa, Weaviate)
"""

import json
import logging
import pickle
import re
from pathlib import Path

from app.indexer import SemanticChunk

logger = logging.getLogger(__name__)

CHROMA_PERSIST_DIR = "/data/chromadb"
BM25_PERSIST_DIR = "/data/bm25"
TOP_K_DEFAULT = 8
MAX_CONTEXT_CHARS = 6000
RRF_K = 60          # standard RRF constant — higher = less penalty for low ranks


def _repo_to_key(repo_full_name: str) -> str:
    """'zetainc-co/nellup' → 'zetainc_co_nellup'"""
    return re.sub(r"[^a-zA-Z0-9_]", "_", repo_full_name).strip("_")


class ContextStore:
    """
    Hybrid vector + BM25 context store.

    Usage:
        store = ContextStore()
        store.upsert_chunks(chunks)                  # index (vector)
        store.build_bm25_index(repo, chunks)         # index (BM25)
        results = store.search(repo, query)           # hybrid retrieval
        store.save_project_context(repo, markdown)   # store generated context
        ctx = store.load_project_context(repo)        # load for review prompt
    """

    def __init__(
        self,
        persist_dir: str = CHROMA_PERSIST_DIR,
        bm25_dir: str = BM25_PERSIST_DIR,
    ):
        self._persist_dir = persist_dir
        self._bm25_dir = bm25_dir
        self._chroma = None
        self._bm25_indexes: dict[str, object] = {}   # in-memory cache
        self._bm25_corpus: dict[str, list[str]] = {}  # doc texts per repo
        self._bm25_ids: dict[str, list[str]] = {}     # chunk IDs parallel to corpus

    # ── ChromaDB (vector) ─────────────────────────────────────────────────────

    def _get_client(self):
        if self._chroma is not None:
            return self._chroma
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        Path(self._persist_dir).mkdir(parents=True, exist_ok=True)
        self._chroma = chromadb.PersistentClient(
            path=self._persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        return self._chroma

    def _get_collection(self, repo_full_name: str):
        client = self._get_client()
        name = _repo_to_key(repo_full_name)
        return client.get_or_create_collection(
            name=name,
            metadata={"repo": repo_full_name, "hnsw:space": "cosine"},
        )

    def upsert_chunks(self, chunks: list[SemanticChunk]) -> int:
        """Upsert semantic chunks into ChromaDB. Returns count indexed."""
        if not chunks:
            return 0
        repo = chunks[0].repo
        collection = self._get_collection(repo)
        BATCH = 500
        total = 0
        for i in range(0, len(chunks), BATCH):
            batch = chunks[i : i + BATCH]
            collection.upsert(
                ids=[c.chunk_id for c in batch],
                documents=[c.summary for c in batch],
                metadatas=[
                    {
                        "file_path": c.file_path,
                        "language": c.language,
                        "node_type": c.node_type,
                        "name": c.name,
                        "start_line": c.start_line,
                        "end_line": c.end_line,
                        "tags": ",".join(c.tags),
                        "content_preview": c.content[:400],
                    }
                    for c in batch
                ],
            )
            total += len(batch)
        logger.info("Upserted %d chunks (vector) for %s", total, repo)
        return total

    def delete_repo(self, repo_full_name: str) -> None:
        """Delete ChromaDB collection + BM25 index for a repo."""
        client = self._get_client()
        name = _repo_to_key(repo_full_name)
        try:
            client.delete_collection(name)
        except Exception:
            pass
        # Clear BM25 in-memory
        self._bm25_indexes.pop(repo_full_name, None)
        self._bm25_corpus.pop(repo_full_name, None)
        self._bm25_ids.pop(repo_full_name, None)
        # Delete persisted BM25
        bm25_path = self._bm25_path(repo_full_name)
        if bm25_path.exists():
            bm25_path.unlink()
        logger.info("Deleted index for %s", repo_full_name)

    def repo_chunk_count(self, repo_full_name: str) -> int:
        try:
            return self._get_collection(repo_full_name).count()
        except Exception:
            return 0

    def list_indexed_repos(self) -> list[str]:
        try:
            return [c.metadata.get("repo", c.name) for c in self._get_client().list_collections()]
        except Exception:
            return []

    # ── BM25 index ────────────────────────────────────────────────────────────

    def _bm25_path(self, repo_full_name: str) -> Path:
        Path(self._bm25_dir).mkdir(parents=True, exist_ok=True)
        return Path(self._bm25_dir) / f"{_repo_to_key(repo_full_name)}.pkl"

    def build_bm25_index(self, repo_full_name: str, chunks: list[SemanticChunk]) -> None:
        """
        Build a BM25 index from chunk summaries.
        Tokenizes by splitting on whitespace + punctuation (simple, fast).
        Persists to disk so it survives worker restarts.
        """
        from rank_bm25 import BM25Okapi

        corpus_texts = [c.summary for c in chunks]
        chunk_ids = [c.chunk_id for c in chunks]
        tokenized = [_tokenize(text) for text in corpus_texts]

        bm25 = BM25Okapi(tokenized)

        # Cache in memory
        self._bm25_indexes[repo_full_name] = bm25
        self._bm25_corpus[repo_full_name] = corpus_texts
        self._bm25_ids[repo_full_name] = chunk_ids

        # Persist to disk
        bm25_path = self._bm25_path(repo_full_name)
        with bm25_path.open("wb") as f:
            pickle.dump({
                "bm25": bm25,
                "corpus": corpus_texts,
                "ids": chunk_ids,
            }, f)

        logger.info("Built BM25 index for %s (%d docs)", repo_full_name, len(chunks))

    def _load_bm25(self, repo_full_name: str) -> bool:
        """Load BM25 index from disk into memory cache. Returns True if loaded."""
        if repo_full_name in self._bm25_indexes:
            return True
        bm25_path = self._bm25_path(repo_full_name)
        if not bm25_path.exists():
            return False
        try:
            with bm25_path.open("rb") as f:
                data = pickle.load(f)
            self._bm25_indexes[repo_full_name] = data["bm25"]
            self._bm25_corpus[repo_full_name] = data["corpus"]
            self._bm25_ids[repo_full_name] = data["ids"]
            return True
        except Exception as exc:
            logger.warning("Could not load BM25 index for %s: %s", repo_full_name, exc)
            return False

    def _bm25_search(self, repo_full_name: str, query: str, top_k: int) -> list[tuple[str, float]]:
        """
        BM25 search. Returns list of (chunk_id, bm25_score) sorted descending.
        """
        if not self._load_bm25(repo_full_name):
            return []
        bm25 = self._bm25_indexes[repo_full_name]
        ids = self._bm25_ids[repo_full_name]
        tokens = _tokenize(query)
        scores = bm25.get_scores(tokens)
        ranked = sorted(zip(ids, scores), key=lambda x: x[1], reverse=True)
        return [(chunk_id, score) for chunk_id, score in ranked[:top_k] if score > 0]

    # ── Hybrid search (RRF) ───────────────────────────────────────────────────

    def search(
        self,
        repo_full_name: str,
        query: str,
        top_k: int = TOP_K_DEFAULT,
    ) -> list[dict]:
        """
        Hybrid search: BM25 + vector, fused with Reciprocal Rank Fusion.

        RRF formula: score(d) = Σ_i  1 / (k + rank_i(d))
        where k=60 (standard), rank_i is the position in list i (1-indexed).

        Returns top_k chunks sorted by RRF score, each as a metadata dict.
        """
        fetch_k = top_k * 3  # fetch more from each retriever before fusion

        # ── Vector search ─────────────────────────────────────────────────────
        vector_results: list[tuple[str, int]] = []  # (chunk_id, rank)
        try:
            collection = self._get_collection(repo_full_name)
            n = min(fetch_k, collection.count() or 1)
            chroma_out = collection.query(
                query_texts=[query],
                n_results=n,
                include=["metadatas", "distances"],
            )
            if chroma_out and chroma_out.get("ids"):
                for rank, chunk_id in enumerate(chroma_out["ids"][0], start=1):
                    vector_results.append((chunk_id, rank))
        except Exception as exc:
            logger.warning("Vector search failed for %s: %s", repo_full_name, exc)

        # ── BM25 search ───────────────────────────────────────────────────────
        bm25_raw = self._bm25_search(repo_full_name, query, fetch_k)
        bm25_results: list[tuple[str, int]] = [
            (chunk_id, rank) for rank, (chunk_id, _) in enumerate(bm25_raw, start=1)
        ]

        # ── RRF fusion ────────────────────────────────────────────────────────
        rrf_scores: dict[str, float] = {}
        for chunk_id, rank in vector_results:
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        for chunk_id, rank in bm25_results:
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)

        # Sort by RRF score descending, take top_k
        sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)[:top_k]

        if not sorted_ids:
            return []

        # ── Fetch metadata for top results ────────────────────────────────────
        try:
            collection = self._get_collection(repo_full_name)
            out = collection.get(ids=sorted_ids, include=["metadatas"])
            if not out or not out.get("metadatas"):
                return []
            results = []
            for chunk_id, meta in zip(out["ids"], out["metadatas"]):
                results.append({
                    **meta,
                    "chunk_id": chunk_id,
                    "rrf_score": round(rrf_scores.get(chunk_id, 0), 6),
                })
            # Re-sort by RRF score (collection.get doesn't guarantee order)
            results.sort(key=lambda x: x["rrf_score"], reverse=True)
            return results
        except Exception as exc:
            logger.warning("Metadata fetch failed for %s: %s", repo_full_name, exc)
            return []

    # ── Project context (Gap 3) ───────────────────────────────────────────────

    def save_project_context(self, repo_full_name: str, context_md: str) -> None:
        """Persist auto-generated project context markdown."""
        path = self._context_path(repo_full_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(context_md, encoding="utf-8")
        logger.info("Saved project context for %s (%d chars)", repo_full_name, len(context_md))

    def load_project_context(self, repo_full_name: str) -> str:
        """Load auto-generated project context. Returns '' if not yet generated."""
        path = self._context_path(repo_full_name)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _context_path(self, repo_full_name: str) -> Path:
        return Path(self._bm25_dir) / f"{_repo_to_key(repo_full_name)}_context.md"


# ── Tokenizer for BM25 ────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """
    Simple tokenizer for code text:
    - Split on whitespace and common punctuation
    - Lowercase
    - Split camelCase and snake_case identifiers
    - Keep tokens >= 2 chars
    """
    # Split camelCase: sendMessage → send message
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Split on punctuation/whitespace
    tokens = re.split(r"[\s\-_./,:;()\[\]{}<>\"'`|\\@#$%^&*+=!?~]+", text.lower())
    return [t for t in tokens if len(t) >= 2]


# ── Module-level singleton ─────────────────────────────────────────────────────

_store: ContextStore | None = None


def get_context_store() -> ContextStore:
    global _store
    if _store is None:
        _store = ContextStore()
    return _store
