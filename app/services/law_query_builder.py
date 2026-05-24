import json
import re
from typing import Any

from app.core.config import get_settings
from app.schemas.pipeline import LLMError
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class LawQueryBuilder:
    def __init__(self, llm_client: LiteLLMClient, prompt_loader: PromptLoader) -> None:
        self.litellm_client = llm_client
        self.prompt_loader = prompt_loader
        self.settings = get_settings()
        self.last_trace: dict[str, Any] = {}

    def build(self, user_text: str, facts: dict[str, Any], legal_area: dict[str, Any]) -> dict[str, Any]:
        prompt_template = self.prompt_loader.load("law_query_builder.md")
        prompt = (
            prompt_template.replace("{{USER_TEXT}}", json.dumps(user_text, ensure_ascii=False))
            .replace("{{FACTS}}", json.dumps(facts, ensure_ascii=False))
            .replace("{{LEGAL_AREA}}", json.dumps(legal_area, ensure_ascii=False))
        )
        try:
            result = self.litellm_client.complete_json(prompt, self.settings.litellm_main_model)
            self.last_trace = self._build_trace(result, fallback_used=False, user_text=user_text, facts=facts, legal_area=legal_area)
            return result
        except LLMError:
            result = self._fallback_query(user_text, facts, legal_area)
            self.last_trace = self._build_trace(result, fallback_used=True, user_text=user_text, facts=facts, legal_area=legal_area)
            return result

    @staticmethod
    def _fallback_query(user_text: str, facts: dict[str, Any], legal_area: dict[str, Any]) -> dict[str, Any]:
        summary = str(facts.get("summary") or user_text).strip()
        lowered = f"{summary} {user_text}".lower()
        keywords = [
            word
            for word in [
                "договор",
                "обязательство",
                "исполнение обязательства",
                "неисполнение обязательства",
                "возврат денежных средств",
                "убытки",
                "срок исполнения",
                "защита гражданских прав",
                "контрагент",
            ]
            if word in lowered
        ]
        if not keywords:
            keywords = [item for item in re.findall(r"[A-Za-zА-Яа-яЁё0-9]{4,}", summary.lower())[:5]]
        primary_area = str(legal_area.get("primary_area") or "")
        case_type = str(facts.get("preliminary_case_type") or "").lower()
        is_contract_dispute = any(token in lowered for token in ["договор", "обязатель", "сайт", "услуг", "исполн"])
        expected_acts = (
            ["ГК РФ"]
            if primary_area in {"civil", "business", "general", "consumer"}
            or case_type in {"service_delay", "refund_request", "price_or_payment_dispute"}
            or is_contract_dispute
            else []
        )
        return {
            "plain_problem": summary,
            "legal_query": " ".join(keywords) or summary,
            "keywords": keywords or ["спор", "обязательство"],
            "expected_acts": expected_acts,
        }

    @staticmethod
    def _detect_scenario(user_text: str, facts: dict[str, Any], legal_area: dict[str, Any]) -> str:
        text = f"{user_text} {facts.get('summary') or ''} {legal_area.get('primary_area') or ''}".lower()
        if any(token in text for token in ["договор", "услуг", "неисполн", "возврат", "убыт"]):
            return "contract_services_nonperformance_refund"
        return "generic"

    @classmethod
    def _build_trace(
        cls,
        result: dict[str, Any],
        fallback_used: bool,
        user_text: str,
        facts: dict[str, Any],
        legal_area: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "query": result.get("legal_query") or result.get("plain_problem"),
            "keywords": result.get("keywords") if isinstance(result.get("keywords"), list) else [],
            "expected_acts_raw": result.get("expected_acts") if isinstance(result.get("expected_acts"), list) else [],
            "expected_act_types_raw": result.get("expected_act_types") if isinstance(result.get("expected_act_types"), list) else [],
            "fallback_used": fallback_used,
            "detected_scenario": cls._detect_scenario(user_text, facts, legal_area),
        }
