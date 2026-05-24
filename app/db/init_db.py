from app.core.logging import setup_logging
from app.core.config import get_settings
from app.db.models import Base
from app.db.session import engine
from sqlalchemy import text


def init_db() -> None:
    setup_logging()
    settings = get_settings()
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)
    if settings.embedding_dim <= 2000:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_law_articles_embedding
                    ON law_articles
                    USING hnsw (embedding vector_cosine_ops)
                    """
                )
            )
    print("Database tables created.")


if __name__ == "__main__":
    init_db()
