"""
Vector memory system — RAG over all company knowledge.
Agents store and retrieve context here.
"""
import json
import structlog
from typing import Optional
from sqlalchemy import text
from app.database import async_session
from app.services.ai_engine import ai

log = structlog.get_logger()


class MemoryService:
    """Persistent vector memory with namespace isolation."""

    async def store(self, namespace: str, key: str, content: str, metadata: dict = None):
        """Store a memory with its embedding."""
        try:
            embedding = await ai.generate_embedding(content)
            async with async_session() as db:
                await db.execute(
                    text("""
                        INSERT INTO memory (namespace, key, content, embedding, metadata, updated_at)
                        VALUES (:ns, :key, :content, :embedding, :meta, NOW())
                        ON CONFLICT (namespace, key)
                        DO UPDATE SET content = :content, embedding = :embedding,
                                      metadata = :meta, updated_at = NOW()
                    """),
                    {
                        "ns": namespace,
                        "key": key,
                        "content": content,
                        "embedding": str(embedding),
                        "meta": json.dumps(metadata or {}),
                    },
                )
                await db.commit()
        except Exception as e:
            log.error("memory.store_failed", namespace=namespace, key=key, error=str(e))

    async def search(self, namespace: str, query: str, limit: int = 5) -> list[dict]:
        """Semantic search within a namespace."""
        try:
            embedding = await ai.generate_embedding(query)
            async with async_session() as db:
                result = await db.execute(
                    text("""
                        SELECT key, content, metadata,
                               1 - (embedding <=> :embedding::vector) as similarity
                        FROM memory
                        WHERE namespace = :ns
                        ORDER BY embedding <=> :embedding::vector
                        LIMIT :limit
                    """),
                    {"ns": namespace, "embedding": str(embedding), "limit": limit},
                )
                rows = result.fetchall()
                return [
                    {
                        "key": r[0],
                        "content": r[1],
                        "metadata": json.loads(r[2]) if isinstance(r[2], str) else r[2],
                        "similarity": round(float(r[3]), 4),
                    }
                    for r in rows
                ]
        except Exception as e:
            log.error("memory.search_failed", namespace=namespace, error=str(e))
            return []

    async def get(self, namespace: str, key: str) -> Optional[str]:
        """Get a specific memory by key."""
        async with async_session() as db:
            result = await db.execute(
                text("SELECT content FROM memory WHERE namespace = :ns AND key = :key"),
                {"ns": namespace, "key": key},
            )
            row = result.fetchone()
            return row[0] if row else None

    async def delete(self, namespace: str, key: str):
        """Delete a memory."""
        async with async_session() as db:
            await db.execute(
                text("DELETE FROM memory WHERE namespace = :ns AND key = :key"),
                {"ns": namespace, "key": key},
            )
            await db.commit()

    async def list_keys(self, namespace: str) -> list[str]:
        """List all keys in a namespace."""
        async with async_session() as db:
            result = await db.execute(
                text("SELECT key FROM memory WHERE namespace = :ns ORDER BY updated_at DESC"),
                {"ns": namespace},
            )
            return [r[0] for r in result.fetchall()]


memory = MemoryService()
