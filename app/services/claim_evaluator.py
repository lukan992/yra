import json
from typing import Any

from app.core.config import get_settings
from app.schemas.pipeline import LLMError
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class ClaimEvaluator:
    def __init__(self, llm_client: LiteLLMClient, prompt_loader: PromptLoader) -> None:
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader
        self.settings = get_settings()

    def evaluate(
        self,
        user_text: str,
        facts: dict[str, Any],
        legal_context: list[dict[str, Any]],
        legal_area: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prompt_template = self.prompt_loader.load("claim_evaluator.md")
        prompt = (
            prompt_template.replace("{{USER_TEXT}}", json.dumps(user_text, ensure_ascii=False))
            .replace("{{FACTS}}", json.dumps(facts, ensure_ascii=False))
            .replace("{{LEGAL_AREA}}", json.dumps(legal_area or {}, ensure_ascii=False))
            .replace("{{LEGAL_CONTEXT}}", json.dumps(legal_context, ensure_ascii=False))
        )
        try:
            return self.llm_client.complete_json(prompt, self.settings.litellm_main_model)
        except LLMError:
            return self._fallback_evaluation(facts, legal_context, legal_area or {})

    @staticmethod
    def _fallback_evaluation(
        facts: dict[str, Any], legal_context: list[dict[str, Any]], legal_area: dict[str, Any]
    ) -> dict[str, Any]:
        consumer_case_types = {
            "defective_goods",
            "defective_service",
            "delivery_delay",
            "service_delay",
            "refund_request",
            "warranty_repair",
            "price_or_payment_dispute",
            "marketplace_dispute",
            "technical_complex_goods",
        }
        case_type = str(facts.get("preliminary_case_type") or "unknown")
        direct_laws = [item for item in legal_context if item.get("applicability") == "direct"]
        if not direct_laws:
            return {
                "status": "need_more_info",
                "recommended_action": "ask_questions",
                "confidence": "low",
                "case_type": case_type,
                "reasoning": "Fallback evaluator: no direct norms were confirmed.",
                "legal_context_assessment": {
                    "has_relevant_laws": False,
                    "relevance_reasoning": "No direct legal basis was available.",
                    "usable_laws": [],
                },
                "missing_required_fields": [{"field": "legal_context", "reason": "Нужна прямая норма права."}],
                "missing_optional_fields": [],
                "clarifying_questions": ClaimEvaluator._fallback_questions(facts),
                "risk_flags": [],
                "error": {"code": None, "message": None},
            }
        if case_type not in consumer_case_types or str(legal_area.get("primary_area") or "") not in {"consumer"}:
            return {
                "status": "route_to_lawyer",
                "recommended_action": "route_to_lawyer",
                "confidence": "medium",
                "case_type": case_type,
                "reasoning": "Fallback evaluator: case is outside the consumer-claim generation scope.",
                "legal_context_assessment": {
                    "has_relevant_laws": True,
                    "relevance_reasoning": "There are norms, but the claim generator scope is conservative.",
                    "usable_laws": direct_laws,
                },
                "missing_required_fields": [],
                "missing_optional_fields": [],
                "clarifying_questions": [],
                "risk_flags": [],
                "error": {"code": None, "message": None},
            }
        return {
            "status": "applicable",
            "recommended_action": "generate_claim",
            "confidence": "low",
            "case_type": case_type,
            "reasoning": "Fallback evaluator: consumer case with direct norms available.",
            "legal_context_assessment": {
                "has_relevant_laws": True,
                "relevance_reasoning": "Direct norms are available in legal context.",
                "usable_laws": direct_laws,
            },
            "missing_required_fields": [],
            "missing_optional_fields": [],
            "clarifying_questions": [],
            "risk_flags": [],
            "error": {"code": None, "message": None},
        }

    @staticmethod
    def _fallback_questions(facts: dict[str, Any]) -> list[str]:
        existing = facts.get("clarifying_questions")
        if isinstance(existing, list) and existing:
            return existing
        return ["Уточните характер договора и какое именно обязательство было нарушено."]
