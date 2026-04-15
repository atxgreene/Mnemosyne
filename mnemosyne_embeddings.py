"""
mnemosyne_embeddings.py — pluggable text → vector embedding.

Purpose
-------
TF-IDF / token-overlap similarity is fine for dream clustering and
casual memory retrieval but caps the semantic sharpness. This module
provides a uniform `Embedder` interface with two backends:

  1. sentence-transformers (optional) — best quality. Enabled if
     the package is installed. Uses `all-MiniLM-L6-v2` by default
     (~80MB, 384-dim, CPU-friendly).
  2. Stdlib hashed bag-of-words (fallback) — deterministic,
     dependency-free, decent recall. Hashes tokens into a
     fixed-dim vector with random-but-stable signs. Works
     everywhere Python runs.

Callers use `embed(text)` or `embed_batch(texts)` and get back
numpy-ish lists of floats (actually `list[float]` for stdlib
portability — we don't import numpy). Similarity is cosine.

Usage
-----
    from mnemosyne_embeddings import get_embedder, cosine

    emb = get_embedder()        # auto-picks best available backend
    v1 = emb.embed("dark mode in terminal")
    v2 = emb.embed("vscode dark theme")
    sim = cosine(v1, v2)        # 0..1

Hook into dreams
----------------
    dreams.consolidate(embedder=emb)   # tighter clusters

Hook into memory search (planned — off by default; requires SQLite
BLOB column + cosine-by-rank, which we won't ship until we have the
eval to prove it beats FTS5 on our workloads).

Stdlib only for the fallback. Optional `sentence-transformers` for
the quality backend.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]{1,}")


# ---- similarity ------------------------------------------------------------

def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity over two equal-length vectors.

    Returns 0.0 on zero vectors rather than raising.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


def normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    if n == 0:
        return list(v)
    return [x / n for x in v]


# ---- stdlib hashed bag-of-words backend ------------------------------------

class HashedBowEmbedder:
    """Token-hash → signed-bin embedder. Fast, dependency-free.

    Each token is hashed to a bin (0..dim-1) via md5(token)[:4] % dim.
    A second hash bit determines the sign contribution (+1 or -1).
    This is the "signed random projection" / "hashing trick"
    approximation — decent for semantic dedup and clustering, poor for
    fine-grained ranking.

    Vectors are L2-normalized.
    """

    name = "hashed-bow"

    def __init__(self, *, dim: int = 256) -> None:
        self.dim = int(dim)

    def _tokens(self, text: str) -> list[str]:
        return [t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1]

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in self._tokens(text):
            h = hashlib.md5(tok.encode("utf-8")).digest()
            bin_idx = int.from_bytes(h[:4], "little") % self.dim
            sign = 1.0 if (h[4] & 1) else -1.0
            vec[bin_idx] += sign
        return normalize(vec)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


# ---- sentence-transformers backend -----------------------------------------

class SentenceTransformersEmbedder:
    """Thin wrapper around sentence-transformers. Falls back to HashedBow
    if import fails at *runtime* (module is installed but model download
    is blocked, for example)."""

    name = "sentence-transformers"

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        # Imported lazily so the module can be pickled / introspected
        # without paying the import cost.
        from sentence_transformers import SentenceTransformer  # type: ignore
        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> list[float]:
        v = self.model.encode(text, convert_to_numpy=False)
        return list(map(float, v))

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vs = self.model.encode(texts, convert_to_numpy=False)
        return [list(map(float, v)) for v in vs]


# ---- factory ---------------------------------------------------------------

def get_embedder(*, prefer: str = "auto") -> Any:
    """Return the best available embedder.

    `prefer` is one of:
      "auto"                  — try sentence-transformers, fall back to hashed-bow
      "sentence-transformers" — raise if not installed
      "hashed-bow"            — always use the stdlib fallback
    """
    if prefer == "hashed-bow":
        return HashedBowEmbedder()

    if prefer in ("auto", "sentence-transformers"):
        try:
            return SentenceTransformersEmbedder()
        except Exception as e:
            if prefer == "sentence-transformers":
                raise RuntimeError(
                    "sentence-transformers is not available: "
                    f"{type(e).__name__}: {e}. Install with "
                    "'pip install sentence-transformers'."
                ) from e

    return HashedBowEmbedder()


def backend_name(embedder: Any) -> str:
    return getattr(embedder, "name", type(embedder).__name__)


# ---- embedding-aware dream clustering --------------------------------------

def cluster_by_embedding(
    memories: list[dict[str, Any]],
    embedder: Any,
    *,
    similarity_threshold: float = 0.55,
    min_cluster_size: int = 3,
) -> list[dict[str, Any]]:
    """Greedy embedding clustering. Same shape as
    mnemosyne_dreams._cluster_memories but uses real vectors.

    Returns a list of {"key", "members", "size", "source_ids"} dicts.
    Stdlib-compatible with the rest of the dreams module.
    """
    if not memories:
        return []

    vectors = embedder.embed_batch([m.get("content", "") or "" for m in memories])

    clusters: list[dict[str, Any]] = []
    cluster_centroids: list[list[float]] = []

    for mem, vec in zip(memories, vectors):
        best_sim = -1.0
        best_idx = -1
        for i, centroid in enumerate(cluster_centroids):
            s = cosine(vec, centroid)
            if s > best_sim:
                best_sim = s
                best_idx = i

        if best_idx >= 0 and best_sim >= similarity_threshold:
            c = clusters[best_idx]
            c["members"].append(mem)
            # Streaming mean of centroid
            n = len(c["members"])
            cluster_centroids[best_idx] = [
                ((n - 1) * a + b) / n
                for a, b in zip(cluster_centroids[best_idx], vec)
            ]
        else:
            content = str(mem.get("content") or "")
            key = " ".join(content.split()[:5]) or "cluster"
            clusters.append({
                "key": key,
                "members": [mem],
                "centroid_note": "embedding",
            })
            cluster_centroids.append(list(vec))

    out: list[dict[str, Any]] = []
    for c in clusters:
        if len(c["members"]) < min_cluster_size:
            continue
        out.append({
            "key": c["key"],
            "members": c["members"],
            "size": len(c["members"]),
            "source_ids": [int(m["id"]) for m in c["members"] if "id" in m],
        })
    return out
