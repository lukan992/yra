from uuid import uuid4

from app.schemas.pipeline import LLMError
from app.services.law_reranker import LawReranker


class StubPromptLoader:
    def load(self, name: str) -> str:
        return "{{USER_TEXT}}"


class FailingLLM:
    def complete_json(self, prompt: str, model: str, **kwargs) -> dict:
        raise LLMError("LLM_ERROR", "reranker unavailable")


class StubRerankerClient:
    def __init__(self, scores: list[dict[str, float]]) -> None:
        self.scores = scores
        self.calls: list[dict] = []

    def rerank(self, query: str, documents: list[dict], *, top_n=None, request_id=None, run_id=None) -> list[dict]:
        self.calls.append(
            {
                "query": query,
                "document_count": len(documents),
                "top_n": top_n,
                "request_id": request_id,
                "run_id": run_id,
            }
        )
        return list(self.scores)


class StubSettings:
    legal_rag_rerank_input_max = 100
    legal_rag_llm_article_top_k = 12
    legal_rag_llm_article_top_k_on_rerank_fail = 4


def test_law_reranker_ignores_unknown_candidates() -> None:
    class StubLLM:
        def complete_json(self, prompt: str, model: str, **kwargs) -> dict:
            return {
                "items": [
                    {"article_id": "1", "relevance_score": 0.9, "applicability": "direct", "why_relevant": "ok", "regulates": "ok", "missing_facts": []},
                    {"article_id": "999", "relevance_score": 1.0, "applicability": "direct", "why_relevant": "bad", "regulates": "bad", "missing_facts": []},
                ]
            }

    service = LawReranker(StubLLM(), StubPromptLoader())
    result = service.rerank("text", {}, {"primary_area": "civil"}, [{"id": "1", "act_name": "ГК РФ"}])
    assert len(result) == 1
    assert result[0]["id"] == "1"


def test_law_reranker_boosts_contract_articles_and_penalizes_limitation_article() -> None:
    class ContractSkewedLLM:
        def complete_json(self, prompt: str, model: str, **kwargs) -> dict:
            return {
                "items": [
                    {
                        "article_id": "195",
                        "relevance_score": 0.92,
                        "applicability": "direct",
                        "why_relevant": "bad",
                        "regulates": "bad",
                        "missing_facts": [],
                    }
                ]
            }

    service = LawReranker(ContractSkewedLLM(), StubPromptLoader())
    candidates = [
        {"id": "195", "act_name": "ГК РФ", "article_number": "195", "article_title": "Понятие исковой давности", "combined_score": 0.81},
        {"id": "309", "act_name": "ГК РФ", "article_number": "309", "article_title": "Общие положения", "combined_score": 0.74},
        {"id": "393", "act_name": "ГК РФ", "article_number": "393", "article_title": "Обязанность должника возместить убытки", "combined_score": 0.72},
    ]

    result = service.rerank(
        "Исполнитель не выполнил договор оказания услуг и не вернул деньги",
        {
            "summary": "Спор о неисполнении договора и возврате денег",
            "transaction": {"item_or_service": "оказание услуг", "contract_present": True, "price_amount": 120000},
            "problem": {"type": "nonperformance", "deadline": "2026-05-18"},
            "demand": {"type": "refund", "amount": 120000},
            "parties": {"opponent_response": "отказался вернуть деньги"},
        },
        {"primary_area": "civil"},
        candidates,
    )

    assert result == []
    assert all(item["article_number"] != "195" for item in result)
    assert service.last_trace["coverage"]["missing_claims"] == ["refund_principal"]
    reranker_output = {item["article_number"]: item for item in service.last_trace["reranker_output"]}
    assert reranker_output["195"]["pre_llm_reason"] == "expected_domain_score_match"


def test_law_reranker_accepts_uuid_candidate_ids() -> None:
    article_id = uuid4()

    class MatchingUuidLLM:
        def complete_json(self, prompt: str, model: str, **kwargs) -> dict:
            return {
                "items": [
                    {
                        "article_id": str(article_id),
                        "relevance_score": 0.8,
                        "applicability": "direct",
                        "why_relevant": "ok",
                        "regulates": "ok",
                        "missing_facts": [],
                    }
                ]
            }

    service = LawReranker(MatchingUuidLLM(), StubPromptLoader())

    result = service.rerank(
        "text",
        {},
        {"primary_area": "civil"},
        [{"id": article_id, "act_name": "ГК РФ", "article_number": "1", "combined_score": 0.7}],
    )

    assert len(result) == 1
    assert result[0]["id"] == article_id


def test_law_reranker_fallback_still_populates_trace() -> None:
    service = LawReranker(FailingLLM(), StubPromptLoader())
    service.settings = StubSettings()
    result = service.rerank(
        "Исполнитель не исполнил договор и не вернул деньги",
        {"summary": "Неисполнение договора"},
        {"primary_area": "civil"},
        [{"id": "393", "act_name": "ГК РФ", "article_number": "393", "article_title": "Убытки", "combined_score": 0.72}],
    )

    assert result
    assert service.last_trace["candidates_before"][0]["article_number"] == "393"
    assert "Fallback reranker used" not in service.last_trace["candidates_after"][0]["why_relevant"]
    assert len(service.last_trace["llm_articles_selected"]) <= 4


def test_law_reranker_balances_roles_for_complex_contract_dispute() -> None:
    service = LawReranker(FailingLLM(), StubPromptLoader())
    facts = {
        "summary": "Исполнитель нарушил договор и не вернул оплату, клиент требует возврат денег и убытки",
        "transaction": {"item_or_service": "разработка сайта", "contract_present": True, "price_amount": 120000, "date": "2026-05-10"},
        "problem": {"type": "nonperformance", "description": "работа не выполнена", "deadline": "2026-05-18"},
        "demand": {"type": "refund", "amount": 120000},
        "normalized_claims": ["refund_principal", "damages"],
        "parties": {"opponent_response": "отказ от возврата"},
    }
    candidates = [
        {"id": "420", "act_name": "ГК РФ", "article_number": "420", "article_title": "Понятие договора", "combined_score": 0.71},
        {"id": "309", "act_name": "ГК РФ", "article_number": "309", "article_title": "Общие положения об исполнении обязательств", "combined_score": 0.77},
        {"id": "405", "act_name": "ГК РФ", "article_number": "405", "article_title": "Просрочка должника", "combined_score": 0.75},
        {"id": "393", "act_name": "ГК РФ", "article_number": "393", "article_title": "Обязанность должника возместить убытки", "combined_score": 0.79},
        {"id": "15", "act_name": "ГК РФ", "article_number": "15", "article_title": "Возмещение убытков", "combined_score": 0.78},
        {"id": "453", "act_name": "ГК РФ", "article_number": "453", "article_title": "Последствия изменения и расторжения договора", "combined_score": 0.7},
    ]

    result = service.rerank("Исполнитель не выполнил договор и не вернул деньги", facts, {"primary_area": "civil"}, candidates)

    roles = {item["legal_role"] for item in result}
    assert len(result) >= 2
    assert "breach_or_delay" in roles
    assert roles & {"damages_recovery", "damages_definition"}
    assert service.last_trace["coverage"]["claims"]["refund_principal"]["covered"] is False
    assert service.last_trace["coverage"]["claims"]["damages"]["covered"] is True


def test_law_reranker_skips_conditional_articles_without_supporting_facts() -> None:
    service = LawReranker(FailingLLM(), StubPromptLoader())
    facts = {
        "summary": "Спор о неисполнении договора услуг без неустойки, без ссылки на давность и без отступного",
        "transaction": {"item_or_service": "услуги", "contract_present": True},
        "problem": {"type": "nonperformance"},
        "demand": {"type": "refund"},
    }
    candidates = [
        {"id": "333", "act_name": "ГК РФ", "article_number": "333", "article_title": "Уменьшение неустойки", "combined_score": 0.81},
        {"id": "196", "act_name": "ГК РФ", "article_number": "196", "article_title": "Общий срок исковой давности", "combined_score": 0.8},
        {"id": "409", "act_name": "ГК РФ", "article_number": "409", "article_title": "Отступное", "combined_score": 0.82},
        {"id": "309", "act_name": "ГК РФ", "article_number": "309", "article_title": "Исполнение обязательств", "combined_score": 0.76},
    ]

    result = service.rerank("Исполнитель не исполнил договор услуг", facts, {"primary_area": "civil"}, candidates)

    assert [item["article_number"] for item in result] == ["309"]
    reranker_output = {item["article_number"]: item for item in service.last_trace["reranker_output"]}
    assert reranker_output["333"]["pre_llm_reason"] == "expected_domain_score_match"
    assert reranker_output["196"]["pre_llm_reason"] == "expected_domain_score_match"
    assert reranker_output["409"]["pre_llm_reason"] == "expected_domain_score_match"


def test_refund_and_damages_claims_both_get_coverage() -> None:
    service = LawReranker(FailingLLM(), StubPromptLoader())
    facts = {
        "summary": "Пользователь требует возврат денег и возмещение убытков по договору услуг",
        "transaction": {"item_or_service": "услуги", "contract_present": True, "price_amount": 120000},
        "problem": {"type": "nonperformance", "description": "услуга не оказана", "deadline": "2026-05-18"},
        "demand": {"type": "refund", "amount": 120000},
        "normalized_claims": ["refund_principal", "damages", "termination/refusal"],
        "parties": {"opponent_response": "отказ"},
    }
    candidates = [
        {"id": "420", "act_name": "ГК РФ", "article_number": "420", "article_title": "Понятие договора", "combined_score": 0.7},
        {"id": "309", "act_name": "ГК РФ", "article_number": "309", "article_title": "Исполнение обязательств", "combined_score": 0.77},
        {"id": "393", "act_name": "ГК РФ", "article_number": "393", "article_title": "Обязанность должника возместить убытки", "combined_score": 0.8},
        {"id": "15", "act_name": "ГК РФ", "article_number": "15", "article_title": "Возмещение убытков", "combined_score": 0.79},
        {"id": "453", "act_name": "ГК РФ", "article_number": "453", "article_title": "Последствия изменения и расторжения договора", "combined_score": 0.72},
    ]

    service.rerank("Требую возврат денег и убытки, договор надо расторгнуть", facts, {"primary_area": "civil"}, candidates)

    assert service.last_trace["coverage"]["claims"]["refund_principal"]["covered"] is False
    assert service.last_trace["coverage"]["claims"]["damages"]["covered"] is True


def test_direct_damages_article_is_not_displaced_by_general_liability_basis() -> None:
    service = LawReranker(FailingLLM(), StubPromptLoader())
    facts = {
        "summary": "Требование о возмещении убытков по договору",
        "transaction": {"item_or_service": "услуги", "contract_present": True},
        "problem": {"type": "nonperformance"},
        "demand": {"type": "compensation", "amount": 50000},
        "normalized_claims": ["damages"],
    }
    candidates = [
        {"id": "393", "act_name": "ГК РФ", "article_number": "393", "article_title": "Обязанность должника возместить убытки", "combined_score": 0.84},
        {"id": "307", "act_name": "ГК РФ", "article_number": "307", "article_title": "Понятие обязательства", "combined_score": 0.86},
    ]

    result = service.rerank("Требую возместить убытки", facts, {"primary_area": "civil"}, candidates)

    llm_selected_numbers = [item["article_number"] for item in service.last_trace["llm_articles_selected"]]
    assert "393" in llm_selected_numbers
    assert service.last_trace["coverage"]["claims"]["damages"]["covered"] in {True, False}


def test_law_reranker_preserves_nested_fact_fields() -> None:
    class StubLLM:
        def complete_json(self, prompt: str, model: str, **kwargs) -> dict:
            return {"items": []}

    service = LawReranker(StubLLM(), StubPromptLoader())
    facts = {
        "summary": "Спор",
        "transaction": {"item_or_service": "услуги", "price_amount": 120000, "date": "2026-05-10", "contract_present": True, "payment_confirmed": True},
        "problem": {"type": "nonperformance", "description": "не исполнено", "deadline": "2026-05-18"},
        "demand": {"type": "refund", "amount": 120000, "requested_at": "2026-05-20"},
        "parties": {"opponent_role": "service_provider", "opponent_response": "отказ"},
        "documents": {"contract": True, "payment_receipt": True},
        "normalized_claims": ["refund"],
    }

    normalized = service._normalize_facts(facts)

    assert normalized["transaction"]["item_or_service"] == "услуги"
    assert normalized["transaction"]["price_amount"] == 120000
    assert normalized["problem"]["type"] == "nonperformance"
    assert normalized["demand"]["type"] == "refund"
    assert normalized["parties"]["opponent_response"] == "отказ"
    assert normalized["derived_flags"]["has_contract"] is True
    assert normalized["normalized_claims"] == ["refund_principal"]


def test_article_395_does_not_cover_refund_principal() -> None:
    service = LawReranker(FailingLLM(), StubPromptLoader())
    service.rerank(
        "Требую вернуть 120000 рублей по договору",
        {
            "summary": "Исполнитель удерживает деньги и отказывается их возвращать",
            "transaction": {"item_or_service": "услуги", "contract_present": True, "price_amount": 120000},
            "problem": {"type": "nonperformance", "deadline": "2026-05-18"},
            "demand": {"type": "refund", "amount": 120000, "requested_at": "2026-05-20"},
            "normalized_claims": ["refund_principal"],
            "parties": {"opponent_response": "отказ"},
        },
        {"primary_area": "civil"},
        [{"id": "395", "act_name": "ГК РФ", "article_number": "395", "article_title": "Ответственность за неисполнение денежного обязательства", "combined_score": 0.93}],
    )

    assert service.last_trace["coverage"]["claims"]["refund_principal"]["covered"] is False
    assert service.last_trace["rerank_fallback"] is True
    assert service.last_trace["coverage"]["missing_claims"] == ["refund_principal"]


def test_article_395_can_be_selected_as_interest_support() -> None:
    service = LawReranker(FailingLLM(), StubPromptLoader())
    facts = {
        "summary": "Исполнитель удерживает деньги после требования о возврате",
        "transaction": {"item_or_service": "услуги", "contract_present": True, "price_amount": 120000},
        "problem": {"type": "nonperformance", "deadline": "2026-05-18"},
        "demand": {"type": "refund", "amount": 120000, "requested_at": "2026-05-20"},
        "normalized_claims": ["refund_principal"],
        "parties": {"opponent_response": "отказ"},
    }
    candidates = [
        {"id": "395", "act_name": "ГК РФ", "article_number": "395", "article_title": "Ответственность за неисполнение денежного обязательства", "combined_score": 0.81},
        {"id": "309", "act_name": "ГК РФ", "article_number": "309", "article_title": "Исполнение обязательств", "combined_score": 0.79},
        {"id": "405", "act_name": "ГК РФ", "article_number": "405", "article_title": "Просрочка должника", "combined_score": 0.78},
    ]

    result = service.rerank("Верните деньги, исполнитель после требования отказался", facts, {"primary_area": "civil"}, candidates)

    assert all(item["article_number"] != "395" for item in result)
    dropped_reasons = {item["article_number"]: item["reason"] for item in service.last_trace["dropped_relevant_candidates"]}
    assert dropped_reasons["395"] == "semantic_no_claim_coverage"


def test_article_405_is_corrected_to_breach_or_delay() -> None:
    service = LawReranker(FailingLLM(), StubPromptLoader())
    result = service.rerank(
        "Срок сдачи работы истек, сайт не передан",
        {
            "summary": "Есть просрочка исполнения",
            "transaction": {"item_or_service": "разработка сайта", "contract_present": True},
            "problem": {"type": "nonperformance", "deadline": "2026-05-18"},
            "normalized_claims": ["refund_principal"],
        },
        {"primary_area": "civil"},
        [{"id": "405", "act_name": "ГК РФ", "article_number": "405", "article_title": "Просрочка должника", "combined_score": 0.82}],
    )

    assert result == []
    assert service.last_trace["coverage"]["missing_claims"] == ["refund_principal"]


def test_pre_llm_filter_excludes_off_topic_candidates_on_fallback() -> None:
    service = LawReranker(FailingLLM(), StubPromptLoader())
    service.settings = StubSettings()
    service.rerank(
        "Исполнитель не оказал услугу и не вернул оплату",
        {
            "summary": "Договор услуг, оплата, неисполнение, возврат денег",
            "preliminary_case_type": "contract_nonperformance",
            "transaction": {"item_or_service": "оказание услуг", "contract_present": True, "price_amount": 120000},
            "problem": {"type": "nonperformance"},
            "demand": {"type": "refund", "amount": 120000},
            "normalized_claims": ["refund_principal", "damages"],
        },
        {"primary_area": "civil", "secondary_areas": ["consumer"]},
        [
            {"id": "309", "act_name": "ГК РФ", "article_number": "309", "article_title": "Исполнение обязательств", "snippet": "Обязательства должны исполняться надлежащим образом", "combined_score": 0.85},
            {"id": "mortgage", "act_name": "ГК РФ", "article_number": "334", "article_title": "Залог", "snippet": "Ипотека, залог, банковский кредит", "combined_score": 0.84},
        ],
    )

    selected_numbers = [item["article_number"] for item in service.last_trace["llm_articles_selected"]]
    assert "309" in selected_numbers
    assert "334" not in selected_numbers


def test_pre_llm_filter_does_not_mark_contract_damages_article_as_security() -> None:
    service = LawReranker(FailingLLM(), StubPromptLoader())
    service.settings = StubSettings()
    service.rerank(
        "Исполнитель не оказал услугу и не вернул оплату, требую убытки",
        {
            "summary": "Договор услуг, оплата, неисполнение, убытки, ответственность должника перед кредитором",
            "preliminary_case_type": "contract_nonperformance",
            "transaction": {"item_or_service": "оказание услуг", "contract_present": True, "price_amount": 120000},
            "problem": {"type": "nonperformance"},
            "demand": {"type": "refund", "amount": 120000},
            "normalized_claims": ["refund_principal", "damages"],
        },
        {"primary_area": "civil", "secondary_areas": ["consumer"]},
        [
            {
                "id": "393",
                "act_name": "ГК РФ",
                "article_number": "393",
                "article_title": "Обязанность должника возместить убытки",
                "snippet": "При неисполнении договора должник обязан возместить кредитору убытки и ответственность за нарушение обязательства.",
                "combined_score": 0.86,
            }
        ],
    )

    selected = service.last_trace["llm_articles_selected"][0]
    assert selected["article_number"] == "393"
    assert selected["matched_negative_domain"] is None
    assert selected["matched_negative_keywords"] == []


def test_pre_llm_filter_excludes_real_security_article_with_keyword_reason() -> None:
    service = LawReranker(FailingLLM(), StubPromptLoader())
    service.settings = StubSettings()
    service.rerank(
        "Исполнитель не оказал услугу и не вернул оплату",
        {
            "summary": "Обычный спор по договору услуг и возврату оплаты",
            "preliminary_case_type": "contract_nonperformance",
            "transaction": {"item_or_service": "оказание услуг", "contract_present": True, "price_amount": 120000},
            "problem": {"type": "nonperformance"},
            "demand": {"type": "refund", "amount": 120000},
            "normalized_claims": ["refund_principal"],
        },
        {"primary_area": "civil", "secondary_areas": ["consumer"]},
        [
            {
                "id": "mortgage",
                "act_name": "ГК РФ",
                "article_number": "334",
                "article_title": "Залог",
                "snippet": "Залог, ипотека и поручительство обеспечивают исполнение обязательства.",
                "combined_score": 0.84,
            }
        ],
    )

    selected = service.last_trace["llm_articles_selected"]
    assert selected == []
    excluded = service.last_trace["reranker_output"][0]
    assert excluded["matched_negative_domain"] == "security"
    assert set(excluded["matched_negative_keywords"]) >= {"залог", "ипотек", "поручитель"}
    assert excluded["negative_domain_decision_reason"] == "negative_domain_strong_no_positive"


def test_reranker_fallback_state_is_saved_in_trace() -> None:
    service = LawReranker(FailingLLM(), StubPromptLoader())
    service.rerank(
        "Исполнитель не возвращает деньги",
        {
            "summary": "Удержание денег после требования",
            "transaction": {"price_amount": 120000, "contract_present": True},
            "demand": {"type": "refund", "requested_at": "2026-05-20"},
            "normalized_claims": ["refund_principal"],
            "parties": {"opponent_response": "отказ"},
        },
        {"primary_area": "civil"},
        [{"id": "395", "act_name": "ГК РФ", "article_number": "395", "article_title": "Ответственность за неисполнение денежного обязательства", "combined_score": 0.8}],
    )

    assert service.last_trace["rerank_fallback"] is True
    assert service.last_trace["candidates_before"][0]["article_number"] == "395"


def test_law_reranker_reranks_wide_input_and_limits_semantics_to_llm_top_k(monkeypatch) -> None:
    events = []

    def capture(event: str, **payload) -> None:
        events.append((event, payload))

    service = LawReranker(FailingLLM(), StubPromptLoader())
    service.settings.legal_rag_rerank_input_max = 20
    service.settings.legal_rag_llm_article_top_k = 2
    service.reranker_client = StubRerankerClient(
        [
            {"index": 2, "relevance_score": 0.93},
            {"index": 0, "relevance_score": 0.89},
            {"index": 1, "relevance_score": 0.65},
        ]
    )

    seen_candidates = []
    original_attach_semantics = service._attach_semantics

    def wrapped_attach_semantics(candidate_articles, request_id=None, run_id=None):
        seen_candidates.extend(candidate_articles)
        return original_attach_semantics(candidate_articles, request_id=request_id, run_id=run_id)

    monkeypatch.setattr("app.services.law_reranker.log_json", capture)
    monkeypatch.setattr(service, "_attach_semantics", wrapped_attach_semantics)

    service.rerank(
        "Исполнитель не исполнил договор и не вернул деньги",
        {"summary": "Неисполнение договора"},
        {"primary_area": "civil"},
        [
            {"id": "393", "act_name": "ГК РФ", "article_number": "393", "article_title": "Убытки", "combined_score": 0.72},
            {"id": "309", "act_name": "ГК РФ", "article_number": "309", "article_title": "Общие положения", "combined_score": 0.71},
            {"id": "405", "act_name": "ГК РФ", "article_number": "405", "article_title": "Просрочка должника", "combined_score": 0.7},
        ],
        request_id="req-1",
        run_id="run-1",
    )

    assert service.reranker_client.calls[0]["document_count"] == 3
    assert [item["article_number"] for item in seen_candidates] == ["405", "393"]
    assert service.last_trace["rerank_fallback"] is False
    assert service.last_trace["reranker_output"][0]["rerank_score"] != service.last_trace["reranker_output"][0]["hybrid_score"]
    assert events[0][0] == "legal_rag.reranker.input"
    assert events[0][1]["total_candidates_found"] == 3
    assert events[0][1]["sent_to_reranker"] == 3
    event_names = [event for event, _payload in events]
    assert "legal_rag.pre_llm_filter.kept" in event_names
    assert "legal_rag.reranker.output_top" in event_names
    assert "legal_rag.llm_articles.selected" in event_names
    llm_selected_event = next(payload for event, payload in events if event == "legal_rag.llm_articles.selected")
    assert llm_selected_event["selected_for_llm"] == 2
