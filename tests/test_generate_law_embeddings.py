import pytest

from app.schemas.pipeline import EmbeddingUnavailableError
from scripts.generate_law_embeddings import generate_embeddings


class Article:
    def __init__(self, article_id: str) -> None:
        self.id = article_id
        self.embedding = None


class StubRepository:
    def __init__(self, articles):
        self.articles = articles

    def get_articles_for_embedding(self, limit: int):
        return self.articles[:limit]

    def _to_candidate(self, article):
        return {"id": article.id, "act_name": "ГК РФ", "article_number": "1", "article_text": "Текст статьи"}


class StubEmbeddingService:
    def __init__(self, vectors=None, error: Exception | None = None) -> None:
        self.vectors = vectors or []
        self.error = error
        self.received_texts = []

    def build_article_text(self, article):
        return f"{article['act_name']} {article['article_number']} {article['article_text']}"

    def embed_texts(self, texts):
        self.received_texts.extend(texts)
        if self.error:
            raise self.error
        return self.vectors


def test_generate_embeddings_fails_without_ollama() -> None:
    repository = StubRepository([Article("1")])
    service = StubEmbeddingService(error=EmbeddingUnavailableError("OLLAMA_UNAVAILABLE", "down"))

    with pytest.raises(EmbeddingUnavailableError):
        generate_embeddings(repository, service, limit=10, batch_size=4)


def test_generate_embeddings_saves_real_vector() -> None:
    article = Article("1")
    repository = StubRepository([article])
    vector = [0.1, 0.2, 0.3]
    service = StubEmbeddingService(vectors=[vector])

    updated = generate_embeddings(repository, service, limit=10, batch_size=4)

    assert updated == 1
    assert article.embedding == vector
    assert service.received_texts
