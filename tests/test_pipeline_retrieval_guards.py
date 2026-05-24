from types import SimpleNamespace

from app.schemas.pipeline import LegalContextNotFoundError
from app.services.law_retriever import LawRetriever


class EmptyHybridRetriever:
    def retrieve(self, query_payload: dict, limit: int = 30) -> list[dict]:
        return []


class EmptyReranker:
    def rerank(self, user_text: str, facts: dict, legal_area: dict, candidate_articles: list[dict]) -> list[dict]:
        return []


class FilledHybridRetriever:
    def retrieve(self, query_payload: dict, limit: int = 30) -> list[dict]:
        return [{"id": "1"}]


def test_pipeline_retriever_fails_when_candidates_missing() -> None:
    service = LawRetriever(EmptyHybridRetriever(), EmptyReranker())
    try:
        service.retrieve("text", {}, {}, {"legal_query": "договор"})
    except LegalContextNotFoundError as exc:
        assert exc.code == "LEGAL_CONTEXT_NOT_FOUND"
    else:
        raise AssertionError("Expected LegalContextNotFoundError")


def test_pipeline_retriever_fails_when_reranker_adds_nothing() -> None:
    service = LawRetriever(FilledHybridRetriever(), EmptyReranker())
    try:
        service.retrieve("text", {}, {}, {"legal_query": "договор"})
    except LegalContextNotFoundError as exc:
        assert exc.code == "LEGAL_CONTEXT_NOT_FOUND"
    else:
        raise AssertionError("Expected LegalContextNotFoundError")
