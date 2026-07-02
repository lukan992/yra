from app.services.hybrid_law_retriever import HybridLawRetriever


class StubRepository:
    def __init__(self):
        self.keyword_calls = []
        self.vector_calls = []

    def get_active_act_names(self):
        return ["ГК РФ"]

    def get_active_act_types(self):
        return ["code"]

    def keyword_search_candidates(self, **kwargs):
        self.keyword_calls.append(kwargs)
        return [{"id": "1", "act_name": "ГК РФ", "keyword_score": 0.9, "vector_score": 0.0}]

    def vector_search_candidates(self, **kwargs):
        self.vector_calls.append(kwargs)
        return [{"id": "1", "act_name": "ГК РФ", "keyword_score": 0.0, "vector_score": 0.7}]


class StubEmbeddingService:
    def __init__(self):
        self.calls = []

    def embed_text(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.1, 0.2]


def test_hybrid_retriever_merges_scores() -> None:
    embedding_service = StubEmbeddingService()
    service = HybridLawRetriever(StubRepository(), embedding_service)
    result = service.retrieve({"legal_query": "договор", "keywords": ["договор"], "expected_acts": ["ГК РФ"]})
    assert result[0]["combined_score"] > 0.7
    assert result[0]["act_match_score"] == 1.0
    assert embedding_service.calls == ["договор договор"]
    assert service.last_trace["merged_candidates"][0]["source"] == "both"
    assert service.last_trace["query_embedding_used"] is True


def test_hybrid_retriever_normalizes_expected_act_aliases() -> None:
    repository = StubRepository()
    service = HybridLawRetriever(repository, StubEmbeddingService())
    result = service.retrieve(
        {"legal_query": "договор", "keywords": ["договор"], "expected_acts": ["Гражданский кодекс РФ"]}
    )

    assert result[0]["act_match_score"] == 1.0
    assert service.last_trace["expected_acts_normalized"] == ["ГК РФ"]
    assert service.last_trace["legal_rag.retriever.query_input"]["expected_acts_used"] == ["ГК РФ"]


def test_hybrid_retriever_drops_unknown_expected_acts_filter() -> None:
    repository = StubRepository()
    service = HybridLawRetriever(repository, StubEmbeddingService())
    service.retrieve(
        {
            "legal_query": "договор",
            "keywords": ["договор"],
            "expected_acts": ["Закон о защите прав потребителей", "Неизвестный акт"],
        }
    )

    assert service.last_trace["expected_acts_normalized"] == []
    assert service.last_trace["legal_rag.retriever.query_input"]["expected_acts_used"] == []


def test_hybrid_retriever_normalizes_expected_act_types_and_single_act_type() -> None:
    repository = StubRepository()
    service = HybridLawRetriever(repository, StubEmbeddingService())
    service.retrieve(
        {
            "legal_query": "договор",
            "keywords": ["договор"],
            "expected_acts": ["ГК РФ"],
            "act_type": "кодекс",
        }
    )

    assert service.last_trace["expected_act_types_normalized"] == ["code"]


def test_hybrid_retriever_broad_expected_act_types_do_not_zero_out_candidates() -> None:
    repository = StubRepository()
    service = HybridLawRetriever(repository, StubEmbeddingService())

    result = service.retrieve(
        {
            "legal_query": "договор услуг возврат оплаты",
            "keywords": ["договор", "услуги", "возврат оплаты"],
            "expected_acts": ["Гражданский кодекс"],
            "expected_act_types": ["Гражданское законодательство", "Законодательство о защите прав потребителей"],
        }
    )

    assert result
    assert service.last_trace["expected_acts_normalized"] == ["ГК РФ"]
    assert service.last_trace["expected_act_types_normalized"] == ["code"]


class ActTypeMismatchRepository(StubRepository):
    def get_active_act_names(self):
        return ["ГК РФ", "Закон о защите прав потребителей"]

    def get_active_act_types(self):
        return ["code", "law"]

    def keyword_search_candidates(self, **kwargs):
        self.keyword_calls.append(kwargs)
        return [{"id": "7", "act_name": "ГК РФ", "act_type": "code", "keyword_score": 0.75, "vector_score": 0.0, "article_title": "Тест"}]

    def vector_search_candidates(self, **kwargs):
        self.vector_calls.append(kwargs)
        return []


def test_hybrid_retriever_relaxed_retry_clears_both_metadata_filters() -> None:
    repository = ActTypeMismatchRepository()
    service = HybridLawRetriever(repository, StubEmbeddingService())

    result = service.retrieve(
        {
            "legal_query": "неисполнение договора",
            "keywords": ["неисполнение", "договор"],
            "expected_acts": ["Закон о защите прав потребителей"],
            "expected_act_types": ["Законодательство о защите прав потребителей"],
            "queries": [{"query": "возврат оплаты по договору услуг", "keywords": ["возврат оплаты"]}],
        }
    )

    assert result
    retry_names = [attempt["name"] for attempt in service.last_trace["attempts"]]
    assert "main_relaxed_all" in retry_names or any(name.endswith("relaxed_all") for name in retry_names)
    assert service.last_trace["dropped_by_reason"]["strict_filter_eliminated_all"] == 1


class StrictFilterRepository(StubRepository):
    def get_active_act_names(self):
        return ["ГК РФ", "Закон о защите прав потребителей"]

    def keyword_search_candidates(self, **kwargs):
        self.keyword_calls.append(kwargs)
        return [{"id": "42", "act_name": "ГК РФ", "keyword_score": 0.8, "vector_score": 0.0, "article_title": "Обязанность", "article_text": "text"}]

    def vector_search_candidates(self, **kwargs):
        self.vector_calls.append(kwargs)
        return []


def test_hybrid_retriever_retries_without_strict_expected_acts_filter_and_logs_reason() -> None:
    repository = StrictFilterRepository()
    service = HybridLawRetriever(repository, StubEmbeddingService())

    result = service.retrieve(
        {
            "legal_query": "неисполнение договора оказания услуг возврат оплаты возмещение убытков",
            "keywords": ["договор оказания услуг", "неисполнение обязательства", "возврат оплаты"],
            "expected_acts": ["Неизвестный акт", "Закон о защите прав потребителей"],
            "queries": [{"query": "возврат оплаты по договору услуг", "keywords": ["возврат оплаты"]}],
        }
    )

    assert result
    assert service.last_trace["dropped_by_reason"]["strict_filter_eliminated_all"] == 0 or service.last_trace["attempts"]
    assert any(attempt["name"] == "main_strict" for attempt in service.last_trace["attempts"])
    assert service.last_trace["raw_keyword_candidates_count"] >= 1
