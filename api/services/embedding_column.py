from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class EmbeddingColumnMismatch(RuntimeError):
    pass


async def check_embedding_column(engine: AsyncEngine, dimension: int) -> None:
    """Refuses to start a process whose embeddings would not fit the column.
    pgvector records a vector column's dimension as its type modifier."""
    async with engine.connect() as connection:
        declared = await connection.scalar(
            text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = to_regclass('source_chunks') "
                "AND attname = 'embedding' AND NOT attisdropped"
            )
        )
    if declared is None:
        raise EmbeddingColumnMismatch(
            "source_chunks.embedding does not exist; run `alembic upgrade head`."
        )
    if declared != dimension:
        raise EmbeddingColumnMismatch(
            f"The embedding provider makes {dimension}-dimensional vectors, but "
            f"source_chunks.embedding is vector({declared}). EMBEDDING_DIMENSION "
            "or EMBEDDING_PROVIDER changed after the migration ran; switching "
            "needs the column migrated and every source re-embedded (see README)."
        )
