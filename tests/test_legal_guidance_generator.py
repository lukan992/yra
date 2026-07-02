from app.services.legal_guidance_generator import LegalGuidanceGenerator


class StubPromptLoader:
    def load(self, name: str) -> str:
        return "{{USER_TEXT}} {{FACTS}} {{LEGAL_AREA}} {{LEGAL_CONTEXT}}"


class EmptyGuidanceLLM:
    def complete_json(self, prompt: str, model: str, **kwargs) -> dict:
        return {
            "document_type": "legal_guidance",
            "status": "legal_guidance",
            "summary": "Нарушено обязательство по договору.",
            "applicable_laws": [],
            "rights": [],
            "recommended_actions": [],
            "risks": [],
        }


def test_legal_guidance_generator_backfills_laws_and_rights_from_legal_context() -> None:
    service = LegalGuidanceGenerator(EmptyGuidanceLLM(), StubPromptLoader())
    legal_context = [
        {
            "id": "law-393",
            "act_name": "ГК РФ",
            "article_number": "393",
            "article_title": "Обязанность должника возместить убытки",
            "why_relevant": "Норма подходит для взыскания убытков.",
        }
    ]

    result = service.generate(
        "Исполнитель не выполнил договор и не вернул деньги",
        {"summary": "Нарушено обязательство по договору."},
        legal_context,
        {"primary_area": "civil"},
    )

    assert result["applicable_laws"][0]["law_name"] == "ГК РФ"
    assert result["applicable_laws"][0]["article_number"] == "393"
    assert any("Согласно ГК РФ, ст. 393" in item for item in result["rights"])
    assert any("потому что Норма подходит для взыскания убытков" in item for item in result["rights"])


def test_legal_guidance_generator_appends_citation_to_existing_right() -> None:
    class ExistingRightsLLM:
        def complete_json(self, prompt: str, model: str, **kwargs) -> dict:
            return {
                "document_type": "legal_guidance",
                "status": "legal_guidance",
                "summary": "Нарушено обязательство по договору.",
                "applicable_laws": [
                    {
                        "law_name": "ГК РФ",
                        "article_number": "15",
                        "article_title": "Возмещение убытков",
                        "why_relevant": "Подходит для описания права на убытки.",
                    }
                ],
                "rights": ["Пользователь вправе требовать возмещения убытков"],
                "recommended_actions": [],
                "risks": [],
            }

    service = LegalGuidanceGenerator(ExistingRightsLLM(), StubPromptLoader())
    result = service.generate("text", {}, [], {"primary_area": "civil"})

    assert result["rights"] == [
        "Пользователь вправе требовать возмещения убытков. Согласно ГК РФ, ст. 15. Эта статья применима, потому что Подходит для описания права на убытки."
    ]


def test_legal_guidance_generator_filters_supporting_laws_from_user_visible_output() -> None:
    service = LegalGuidanceGenerator(EmptyGuidanceLLM(), StubPromptLoader())
    legal_context = [
        {
            "id": "law-393",
            "act_name": "ГК РФ",
            "article_number": "393",
            "article_title": "Обязанность должника возместить убытки",
            "why_relevant": "Статья прямо позволяет требовать возмещение убытков.",
            "coverage_type": "direct",
            "coverage": [
                {
                    "claim": "damages",
                    "coverage_type": "direct",
                    "counts_as_covered": True,
                    "effect_type": "damages_recovery",
                    "effect_description": "Право требовать возмещения убытков.",
                    "trigger_conditions": [],
                    "missing_facts": [],
                }
            ],
        },
        {
            "id": "law-328",
            "act_name": "ГК РФ",
            "article_number": "328",
            "article_title": "Встречное исполнение обязательства",
            "why_relevant": "Поддерживающий контекст.",
            "coverage_type": "supporting",
            "coverage": [
                {
                    "claim": "damages",
                    "coverage_type": "supporting",
                    "counts_as_covered": False,
                    "effect_type": "performance_terms",
                    "effect_description": "Правила исполнения обязательства.",
                    "trigger_conditions": [],
                    "missing_facts": [],
                }
            ],
        },
    ]

    result = service.generate(
        "Исполнитель не выполнил договор и не вернул деньги",
        {"summary": "Нарушено обязательство по договору.", "normalized_claims": ["damages"]},
        legal_context,
        {"primary_area": "civil"},
    )

    assert [item["article_number"] for item in result["applicable_laws"]] == ["393"]


def test_legal_guidance_generator_separates_refund_and_damages_when_damages_details_missing() -> None:
    service = LegalGuidanceGenerator(EmptyGuidanceLLM(), StubPromptLoader())
    legal_context = [
        {
            "id": "law-refund",
            "act_name": "ГК РФ",
            "article_number": "453",
            "article_title": "Последствия изменения и расторжения договора",
            "why_relevant": "Возврат уплаченной суммы при прекращении договора.",
            "coverage_type": "valid_conditional",
            "coverage": [
                {
                    "claim": "refund_principal",
                    "coverage_type": "valid_conditional",
                    "counts_as_covered": True,
                    "effect_type": "termination_consequences",
                    "effect_description": "Право требовать возврата уплаченной суммы.",
                    "trigger_conditions": ["termination_or_refusal"],
                    "missing_facts": [],
                }
            ],
        },
        {
            "id": "law-393",
            "act_name": "ГК РФ",
            "article_number": "393",
            "article_title": "Обязанность должника возместить убытки",
            "why_relevant": "Статья прямо позволяет требовать возмещение убытков.",
            "coverage_type": "direct",
            "coverage": [
                {
                    "claim": "damages",
                    "coverage_type": "direct",
                    "counts_as_covered": True,
                    "effect_type": "damages_recovery",
                    "effect_description": "Право требовать возмещения убытков.",
                    "trigger_conditions": [],
                    "missing_facts": [],
                }
            ],
        },
    ]

    result = service.generate(
        "Исполнитель не выполнил договор и не вернул деньги",
        {
            "summary": "Исполнитель не выполнил договор и не вернул деньги.",
            "normalized_claims": ["refund_principal", "damages"],
            "missing_fields": [{"field": "Подробности убытков", "reason": "Нужно уточнить состав и размер убытков."}],
            "clarifying_questions": ["Какие именно убытки, кроме суммы оплаты, вы понесли и чем они подтверждаются?"],
        },
        legal_context,
        {"primary_area": "civil"},
    )

    assert any("возврату оплаты" in item.lower() for item in result["rights"])
    assert any("убытков" in item.lower() and "нужно уточнить состав, размер" in item.lower() for item in result["rights"])
    assert any("убытков уточните их состав" in item.lower() for item in result["recommended_actions"])
    assert result["clarifying_questions"] == [
        "Какие именно убытки, кроме суммы оплаты, вы понесли и чем они подтверждаются?"
    ]
