from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.session import SessionLocal
from app.repositories.law_repository import LawRepository
from app.schemas.pipeline import EmbeddingUnavailableError
from app.services.embedding_service import EmbeddingService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate embeddings for law_articles with missing or outdated embeddings.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=8)
    return parser.parse_args()


def generate_embeddings(repository: LawRepository, embedding_service: EmbeddingService, limit: int, batch_size: int) -> int:
    articles = repository.get_articles_for_embedding(limit=limit)
    updated = 0
    for start in range(0, len(articles), batch_size):
        batch = articles[start : start + batch_size]
        texts = [embedding_service.build_article_text(repository._to_candidate(article)) for article in batch]
        vectors = embedding_service.embed_texts(texts)
        for article, vector in zip(batch, vectors):
            article.embedding = vector
            updated += 1
    return updated


def main() -> None:
    args = parse_args()
    db = SessionLocal()
    repository = LawRepository(db)
    embedding_service = EmbeddingService()
    updated = 0
    try:
        updated = generate_embeddings(repository, embedding_service, limit=args.limit, batch_size=args.batch_size)
        db.commit()
    except EmbeddingUnavailableError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(json.dumps({"updated": updated}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
