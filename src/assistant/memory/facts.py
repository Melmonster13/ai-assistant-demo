"""Fact-recall store: durable facts with semantic search over pgvector.

Every operation scopes the connection to one user via the app.current_user_id
session setting; Postgres RLS (see 03_memory.sql) enforces isolation structurally,
so a query can only ever touch that user's rows even if application code forgot a
WHERE clause. The connection must be a non-superuser role or RLS is bypassed.
"""

from dataclasses import dataclass
from typing import Any

from pgvector.psycopg import register_vector

from assistant.memory.embeddings import Embedder


@dataclass
class Fact:
    content: str
    similarity: float


class FactStore:
    def __init__(self, conn: Any, embedder: Embedder) -> None:
        self._conn = conn
        self._embedder = embedder
        register_vector(conn)

    def _scope(self, cur: Any, user_id: str) -> None:
        # per-request user binding that the RLS policy reads
        cur.execute("SELECT set_config('app.current_user_id', %s, false)", (user_id,))

    def remember(self, user_id: str, content: str) -> int:
        embedding = self._embedder.embed(content)
        with self._conn.cursor() as cur:
            self._scope(cur, user_id)
            cur.execute(
                "INSERT INTO facts (user_id, content, embedding) VALUES (%s, %s, %s) RETURNING id",
                (user_id, content, embedding),
            )
            return cur.fetchone()[0]

    def browse(self, user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Most recent facts for the read-only memory browser (RLS-scoped)."""
        with self._conn.cursor() as cur:
            self._scope(cur, user_id)
            cur.execute(
                "SELECT content, created_at FROM facts ORDER BY created_at DESC, id DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
        return [{"content": content, "created_at": created.isoformat()} for content, created in rows]

    def recall(self, user_id: str, query: str, *, k: int = 5, min_similarity: float = 0.0) -> list[Fact]:
        embedding = self._embedder.embed(query)
        with self._conn.cursor() as cur:
            self._scope(cur, user_id)
            cur.execute(
                "SELECT content, 1 - (embedding <=> %s) AS similarity"
                " FROM facts ORDER BY embedding <=> %s LIMIT %s",
                (embedding, embedding, k),
            )
            rows = cur.fetchall()
        return [Fact(content, float(sim)) for content, sim in rows if sim >= min_similarity]
