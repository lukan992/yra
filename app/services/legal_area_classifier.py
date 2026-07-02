import json
from typing import Any

from app.core.config import get_settings
from app.schemas.pipeline import LLMError
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class LegalAreaClassifier:
    def __init__(self, llm_client: LiteLLMClient, prompt_loader: PromptLoader) -> None:
        self.litellm_client = llm_client
        self.prompt_loader = prompt_loader
        self.settings = get_settings()

    def classify(self, user_text: str, facts: dict[str, Any]) -> dict[str, Any]:
        prompt_template = self.prompt_loader.load("legal_area_classifier.md")
        prompt = (
            prompt_template.replace("{{USER_TEXT}}", json.dumps(user_text, ensure_ascii=False))
            .replace("{{FACTS}}", json.dumps(facts, ensure_ascii=False))
        )
        try:
            return self.litellm_client.complete_json(
                prompt,
                self.settings.legal_area_classifier_model,
                stage="legal_area_classification",
            )
        except LLMError:
            return self._fallback_classification(user_text, facts)

    @staticmethod
    def _fallback_classification(user_text: str, facts: dict[str, Any]) -> dict[str, Any]:
        case_type = str(facts.get("preliminary_case_type") or "").lower()
        text = f"{user_text} {facts.get('summary') or ''}".lower()
        if "труд" in text or case_type == "labor_rights":
            primary = "labor"
        elif case_type in {
            "defective_goods",
            "defective_service",
            "delivery_delay",
            "service_delay",
            "refund_request",
            "warranty_repair",
            "price_or_payment_dispute",
            "marketplace_dispute",
            "technical_complex_goods",
        }:
            primary = "consumer"
        elif "договор" in text or "обязатель" in text or "контрагент" in text:
            primary = "civil"
        else:
            primary = "general"
        return {
            "primary_area": primary,
            "secondary_areas": ["business"] if "контрагент" in text and primary == "civil" else [],
            "confidence": 0.35,
            "reason": "Fallback classification used because the LLM classifier was unavailable.",
        }
