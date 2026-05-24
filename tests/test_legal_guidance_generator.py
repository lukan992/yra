from app.services.legal_guidance_generator import LegalGuidanceGenerator


class StubPromptLoader:
    def load(self, name: str) -> str:
        return "{{USER_TEXT}} {{FACTS}} {{LEGAL_AREA}} {{LEGAL_CONTEXT}}"


class EmptyGuidanceLLM:
    def complete_json(self, prompt: str, model: str) -> dict:
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
        def complete_json(self, prompt: str, model: str) -> dict:
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
