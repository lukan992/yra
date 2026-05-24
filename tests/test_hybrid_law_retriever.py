from app.services.hybrid_law_retriever import HybridLawRetriever


class StubRepository:
    def __init__(self):
        self.last_keyword_expected_acts = None
        self.last_vector_expected_acts = None
        self.last_keyword_expected_act_types = None
        self.last_vector_expected_act_types = None

    def get_active_act_names(self):
        return ["ГК РФ"]

    def keyword_search_candidates(self, **kwargs):
        self.last_keyword_expected_acts = kwargs.get("expected_acts")
        self.last_keyword_expected_act_types = kwargs.get("expected_act_types")
        return [{"id": "1", "act_name": "ГК РФ", "keyword_score": 0.9, "vector_score": 0.0}]

    def vector_search_candidates(self, **kwargs):
        self.last_vector_expected_acts = kwargs.get("expected_acts")
        self.last_vector_expected_act_types = kwargs.get("expected_act_types")
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
    assert repository.last_keyword_expected_acts == ["ГК РФ"]
    assert repository.last_vector_expected_acts == ["ГК РФ"]


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

    assert repository.last_keyword_expected_acts == []
    assert repository.last_vector_expected_acts == []


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

    assert repository.last_keyword_expected_act_types == ["code"]
    assert repository.last_vector_expected_act_types == ["code"]
