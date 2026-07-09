"""Embedding adapter — provider is config, not architecture (same pattern as the
model adapter). `local` uses a small local sentence-transformers model (private,
no API key, consistent with the local-first voice stance); `hashing` is a
zero-dependency bag-of-words fallback used in tests and light setups.

All backends return L2-normalized float32 vectors of EMBED_DIM, so cosine
distance in pgvector is a dot product and the schema's vector(384) column is
backend-independent.
"""

import hashlib
import re
from typing import Protocol

import numpy as np

EMBED_DIM = 384


class Embedder(Protocol):
    def embed(self, text: str) -> np.ndarray: ...


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    if norm == 0:
        return vec.astype(np.float32)
    return (vec / norm).astype(np.float32)


class HashingEmbedder:
    """Deterministic hashing vectorizer: tokens are hashed into buckets, so
    cosine similarity tracks token overlap. Not a semantic model — enough for
    deterministic, offline tests and a no-dependency fallback."""

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self._dim = dim

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self._dim, dtype=np.float32)
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest, "big") % self._dim
            vec[bucket] += 1.0
        return _normalize(vec)


class LocalEmbedder:
    """sentence-transformers backend. Imported lazily so the memory package (and
    tests) don't require torch unless this backend is actually selected."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        # method was renamed across versions; support both
        get_dim = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
        dim = get_dim()
        if dim != EMBED_DIM:
            raise ValueError(f"model '{model_name}' embeds to {dim} dims, schema expects {EMBED_DIM}")

    def embed(self, text: str) -> np.ndarray:
        return _normalize(np.asarray(self._model.encode(text), dtype=np.float32))


def make_embedder(backend: str) -> Embedder:
    if backend == "hashing":
        return HashingEmbedder()
    if backend == "local":
        try:
            return LocalEmbedder()
        except ImportError as exc:
            raise SystemExit(
                "EMBEDDING_BACKEND=local needs the local-embeddings extra: "
                "`uv sync --extra local-embeddings` (or set EMBEDDING_BACKEND=hashing)"
            ) from exc
    raise ValueError(f"unknown embedding backend: {backend!r}")
