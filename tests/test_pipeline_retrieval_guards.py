from types import SimpleNamespace

from app.schemas.pipeline import LegalContextNotFoundError, QueryBuilderError
from app.schemas.responses import ErrorResponse
from app.services.pipeline import ClaimPipeline
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
        service.retrieve("text", {}, {}, {"legal_query": "договор", "keywords": ["договор"]})
    except LegalContextNotFoundError as exc:
        assert exc.code == "LEGAL_CONTEXT_NOT_FOUND"
    else:
        raise AssertionError("Expected LegalContextNotFoundError")


def test_pipeline_retriever_fails_when_reranker_adds_nothing() -> None:
    service = LawRetriever(FilledHybridRetriever(), EmptyReranker())
    try:
        service.retrieve("text", {}, {}, {"legal_query": "договор", "keywords": ["договор"]})
    except LegalContextNotFoundError as exc:
        assert exc.code == "LEGAL_CONTEXT_NOT_FOUND"
    else:
        raise AssertionError("Expected LegalContextNotFoundError")


def test_pipeline_retriever_fails_fast_for_empty_query_payload() -> None:
    service = LawRetriever(FilledHybridRetriever(), EmptyReranker())
    try:
        service.retrieve("text", {}, {}, {})
    except QueryBuilderError as exc:
        assert exc.code == "QUERY_BUILDER_EMPTY_PAYLOAD"
    else:
        raise AssertionError("Expected QueryBuilderError")


def test_pipeline_query_guard_repairs_empty_query_payload_before_retrieval() -> None:
    pipeline = ClaimPipeline.__new__(ClaimPipeline)
    pipeline.law_query_builder = type(
        "QueryBuilder",
        (),
        {
            "last_trace": {},
            "ensure_query_payload": lambda self, user_text, facts, legal_area, query_payload, fallback_reason="": {
                "plain_problem": "Спор по договору услуг",
                "legal_query": "неисполнение договора оказания услуг возврат оплаты возмещение убытков просрочка исполнения",
                "keywords": ["договор оказания услуг", "неисполнение обязательства"],
                "expected_acts": ["ГК РФ"],
                "fallback_used": True,
            },
        },
    )()

    repaired = ClaimPipeline._ensure_query_payload(
        pipeline,
        "Я заключил договор на оказание услуг, оплатил работу, но исполнитель не выполнил обязательство в срок и деньги не возвращает",
        {"normalized_claims": ["refund_principal", "damages"]},
        {"primary_area": "civil", "detected_claims": ["refund_principal", "damages"], "domain_signals": ["contract", "payment", "nonperformance"]},
        {},
        "req-1",
        "run-1",
    )

    assert repaired["legal_query"]
    assert repaired["keywords"]


def test_pipeline_error_response_for_missing_legal_context_does_not_ask_for_known_facts() -> None:
    pipeline = ClaimPipeline.__new__(ClaimPipeline)

    response = ClaimPipeline._response(
        pipeline,
        "error",
        "req-1",
        "run-1",
        facts={
            "summary": "Есть договор, оплата и отказ вернуть деньги",
            "missing_fields": [{"field": "Подробности убытков", "reason": "Уточнить убытки"}],
            "clarifying_questions": ["Какие именно убытки вы понесли?"],
        },
        error=ErrorResponse(code="LEGAL_CONTEXT_NOT_FOUND", message="Не удалось подобрать релевантные нормы права."),
        context_validation={},
    )

    assert response.error is not None
    assert response.error.code == "LEGAL_CONTEXT_NOT_FOUND"
    assert response.missing_fields == []
    assert response.clarifying_questions == []


def test_pipeline_user_visible_articles_exclude_supporting_entries(monkeypatch) -> None:
    events = []

    def capture(event: str, **payload):
        events.append((event, payload))

    pipeline = ClaimPipeline.__new__(ClaimPipeline)
    pipeline.settings = type("Settings", (), {"log_rag_trace": True, "log_rag_trace_full": False})()
    monkeypatch.setattr("app.services.pipeline.log_json", capture)

    visible = ClaimPipeline._filter_user_visible_articles(
        pipeline,
        [
            {
                "id": "law-393",
                "act_name": "ГК РФ",
                "article_number": "393",
                "article_title": "Обязанность должника возместить убытки",
                "coverage_type": "direct",
                "coverage": [
                    {
                        "claim": "damages",
                        "coverage_type": "direct",
                        "effect_type": "damages_recovery",
                        "effect_scope": "general_direct",
                        "missing_conditions": [],
                        "trigger_conditions_satisfied": True,
                        "user_visible": True,
                    }
                ],
            },
            {
                "id": "law-328",
                "act_name": "ГК РФ",
                "article_number": "328",
                "article_title": "Встречное исполнение обязательства",
                "coverage_type": "supporting",
                "coverage": [
                    {
                        "claim": "damages",
                        "coverage_type": "supporting",
                        "effect_type": "performance_terms",
                        "effect_scope": "general_direct",
                        "missing_conditions": [],
                        "trigger_conditions_satisfied": True,
                        "user_visible": False,
                    }
                ],
            },
        ],
        "req-1",
        "run-1",
    )

    assert [item["article_number"] for item in visible] == ["393"]
    assert events[-1][0] == "legal_rag.formatter.user_visible_articles"
    logged_articles = events[-1][1]["articles"]
    assert any(item["article_number"] == "328" and item["user_visible"] is False for item in logged_articles)
