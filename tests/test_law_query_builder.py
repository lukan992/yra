from app.services.law_query_builder import LawQueryBuilder


class StubLLM:
    def complete_json(self, prompt: str, model: str, **kwargs) -> dict:
        return {
            "plain_problem": "Спор по договору",
            "legal_query": "нарушение договора защита прав",
            "keywords": ["договор", "защита прав"],
            "expected_acts": ["ГК РФ"],
        }


class StubPromptLoader:
    def load(self, name: str) -> str:
        return "{{USER_TEXT}} {{FACTS}} {{LEGAL_AREA}}"


def test_law_query_builder_does_not_emit_article_numbers() -> None:
    service = LawQueryBuilder(StubLLM(), StubPromptLoader())
    result = service.build("Нарушен договор", {}, {"primary_area": "civil"})
    combined = " ".join(result["keywords"]) + " " + result["legal_query"]
    assert "статья" not in combined.lower()
    assert "307" not in combined
    assert service.last_trace["query"] == result["legal_query"]
    assert service.last_trace["expected_acts_raw"] == ["ГК РФ"]
    assert service.last_trace["fallback_used"] is False


def test_law_query_builder_fallback_targets_gk_rf_for_contract_dispute() -> None:
    class FailingLLM:
        def complete_json(self, prompt: str, model: str, **kwargs) -> dict:
            raise Exception("should not be used directly")

    service = LawQueryBuilder(FailingLLM(), StubPromptLoader())
    result = service._fallback_query(
        "Исполнитель не выполнил договор на разработку сайта и не вернул деньги",
        {"summary": "Неисполнение договора на разработку сайта", "preliminary_case_type": "service_delay"},
        {"primary_area": "consumer", "secondary_areas": ["consumer"], "detected_claims": ["refund_principal", "damages"]},
    )

    assert result["legal_query"] == "неисполнение договора оказания услуг возврат оплаты возмещение убытков просрочка исполнения"
    assert result["expected_acts"] == ["ГК РФ", "Закон о защите прав потребителей"]
    assert "договор оказания услуг" in result["keywords"]
    assert result["fallback_used"] is True


def test_law_query_builder_replaces_empty_llm_payload_with_fallback() -> None:
    class EmptyLLM:
        def complete_json(self, prompt: str, model: str, **kwargs) -> dict:
            return {}

    service = LawQueryBuilder(EmptyLLM(), StubPromptLoader())
    result = service.build(
        "Я заключил договор на оказание услуг, оплатил работу, но исполнитель не выполнил обязательство в срок и деньги не возвращает",
        {"summary": "Договор услуг, оплата, неисполнение, отказ вернуть деньги", "normalized_claims": ["refund_principal", "damages"]},
        {"primary_area": "civil", "secondary_areas": ["consumer"], "detected_claims": ["refund_principal", "damages"], "domain_signals": ["contract", "payment", "nonperformance"]},
    )

    assert result["legal_query"]
    assert result["keywords"]
    assert service.last_trace["fallback_used"] is True
    assert service.last_trace["fallback_reason"] == "empty_payload"


def test_law_query_builder_trace_marks_contract_fallback_scenario() -> None:
    service = LawQueryBuilder(StubLLM(), StubPromptLoader())
    trace = service._build_trace(
        {
            "plain_problem": "Спор по договору услуг",
            "legal_query": "договор услуги возврат денег",
            "keywords": ["договор", "услуги", "возврат"],
            "expected_acts": ["ГК РФ"],
        },
        fallback_used=True,
        fallback_reason="empty_payload",
        user_text="Исполнитель не выполнил договор услуг и не вернул деньги",
        facts={"summary": "Неисполнение договора услуг"},
        legal_area={"primary_area": "civil"},
    )

    assert trace["fallback_used"] is True
    assert trace["fallback_reason"] == "empty_payload"
    assert trace["detected_scenario"] == "contract_services_nonperformance_refund"
