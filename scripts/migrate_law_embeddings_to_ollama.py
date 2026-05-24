from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine


def main() -> None:
    settings = get_settings()
    index_created = False
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("DROP INDEX IF EXISTS idx_law_articles_embedding"))
        connection.execute(text("UPDATE law_articles SET embedding = NULL"))
        connection.execute(
            text(
                f"""
                ALTER TABLE law_articles
                ALTER COLUMN embedding TYPE vector({settings.embedding_dim})
                USING CASE
                    WHEN embedding IS NULL THEN NULL::vector({settings.embedding_dim})
                    ELSE embedding::text::vector({settings.embedding_dim})
                END
                """
            )
        )
        if settings.embedding_dim <= 2000:
            connection.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_law_articles_embedding
                    ON law_articles
                    USING hnsw (embedding vector_cosine_ops)
                    """
                )
            )
            index_created = True
    print(
        json.dumps(
            {
                "status": "ok",
                "embedding_dim": settings.embedding_dim,
                "index_created": index_created,
                "index_reason": None if index_created else "pgvector HNSW supports at most 2000 dimensions",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
