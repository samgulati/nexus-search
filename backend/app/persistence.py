from __future__ import annotations

from collections.abc import Iterable

import psycopg

from .config import settings
from .models import Document


class PostgresDocumentStore:
    """Durable shard document storage backed by PostgreSQL.

    PostgreSQL stores the canonical shard-owned documents. The BM25 and
    semantic search structures stay in memory and are rebuilt from these
    rows whenever a shard starts.
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url.strip()

    @property
    def enabled(self) -> bool:
        return bool(self.database_url)

    def _connect(self):
        return psycopg.connect(self.database_url)

    def initialize(self) -> None:
        if not self.enabled:
            return

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS nexus_documents (
                        shard_id TEXT NOT NULL,
                        document_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        body TEXT NOT NULL,
                        url TEXT,
                        source TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        indexed_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY (shard_id, document_id),
                        UNIQUE (shard_id, content_hash)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_nexus_documents_shard_id
                    ON nexus_documents (shard_id)
                    """
                )

    def load_shard(self, shard_id: str) -> list[Document]:
        if not self.enabled:
            return []

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        document_id,
                        title,
                        body,
                        url,
                        source,
                        content_hash,
                        indexed_at
                    FROM nexus_documents
                    WHERE shard_id = %s
                    ORDER BY indexed_at, document_id
                    """,
                    (str(shard_id),),
                )
                rows = cur.fetchall()

        return [
            Document(
                id=row[0],
                title=row[1],
                text=row[2],
                url=row[3],
                source=row[4],
                content_hash=row[5],
                indexed_at=row[6],
            )
            for row in rows
        ]

    def upsert(self, shard_id: str, document: Document) -> int:
        return self.upsert_many(shard_id, [document])

    def upsert_many(self, shard_id: str, documents: Iterable[Document]) -> int:
        if not self.enabled:
            return 0

        docs = list(documents)
        if not docs:
            return 0

        rows = [
            (
                str(shard_id),
                doc.id,
                doc.title,
                doc.text,
                doc.url,
                doc.source,
                doc.content_hash,
                doc.indexed_at,
            )
            for doc in docs
        ]

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO nexus_documents (
                        shard_id, document_id, title, body, url, source,
                        content_hash, indexed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (shard_id, content_hash) DO UPDATE SET
                        document_id = EXCLUDED.document_id,
                        title = EXCLUDED.title,
                        body = EXCLUDED.body,
                        url = EXCLUDED.url,
                        source = EXCLUDED.source,
                        indexed_at = EXCLUDED.indexed_at
                    """,
                    rows,
                )

        return len(rows)


document_store = PostgresDocumentStore(settings.database_url)
