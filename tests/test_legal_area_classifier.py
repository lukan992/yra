from app.services.legal_area_classifier import LegalAreaClassifier


class StubLLM:
    def complete_json(self, prompt: str, model: str, **kwargs) -> dict:
        return {"primary_area": "civil", "secondary_areas": ["business"], "confidence": 0.8, "reason": "ok"}


class StubPromptLoader:
    def load(self, name: str) -> str:
        return "USER={{USER_TEXT}} FACTS={{FACTS}}"


def test_legal_area_classifier_returns_strict_json() -> None:
    service = LegalAreaClassifier(StubLLM(), StubPromptLoader())
    result = service.classify("Спор по договору", {"summary": "договор"})
    assert result["primary_area"] == "civil"
    assert result["confidence"] == 0.8
