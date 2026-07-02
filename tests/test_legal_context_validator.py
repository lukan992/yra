from app.services.legal_context_validator import LegalContextValidator


class StubLLM:
    def complete_json(self, prompt: str, model: str, **kwargs) -> dict:
        return {"status": "needs_clarification", "confidence": 0.4, "has_direct_basis": False, "needs_clarification": True, "missing_facts": ["срок"], "warnings": ["низкая релевантность"]}


class StubPromptLoader:
    def load(self, name: str) -> str:
        return "{{USER_TEXT}}"


def test_legal_context_validator_returns_need_more_info_signal() -> None:
    service = LegalContextValidator(StubLLM(), StubPromptLoader())
    result = service.validate("text", {}, {"primary_area": "civil"}, [{"id": "1"}])
    assert result["status"] == "needs_clarification"
    assert result["missing_facts"] == ["срок"]
