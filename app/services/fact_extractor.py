import json
import re
from typing import Any

from app.core.config import get_settings
from app.schemas.pipeline import LLMError
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class FactExtractor:
    def __init__(self, llm_client: LiteLLMClient, prompt_loader: PromptLoader) -> None:
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader
        self.settings = get_settings()

    def extract(self, user_text: str) -> dict[str, Any]:
        prompt_template = self.prompt_loader.load("fact_extractor.md")
        user_text_json = json.dumps(user_text, ensure_ascii=False)
        prompt = prompt_template.replace("{{USER_TEXT}}", user_text_json)
        try:
            result = self.llm_client.complete_json(prompt, self.settings.litellm_main_model)
        except LLMError:
            result = self._fallback_extract(user_text)
        return self._postprocess_facts(result, user_text)

    @staticmethod
    def _fallback_extract(user_text: str) -> dict[str, Any]:
        text = user_text.strip()
        lowered = text.lower()
        if any(token in lowered for token in ["работодатель", "зарплат", "увольн", "труд"]):
            case_type = "labor_rights"
            problem_type = "salary_delay" if "зарплат" in lowered else "other"
            opponent_role = "employer"
        elif any(token in lowered for token in ["товар", "магазин", "продав", "услуг", "маркетплейс"]):
            case_type = "price_or_payment_dispute" if "деньг" in lowered else "refund_request"
            problem_type = "refusal" if "не возвращ" in lowered or "отказ" in lowered else "other"
            opponent_role = "seller"
        else:
            case_type = "outside_zopp_scope"
            problem_type = "refusal" if "не возвращ" in lowered or "не испол" in lowered else "other"
            opponent_role = "unknown"

        demand_type = "refund" if "деньг" in lowered or "возврат" in lowered else "unknown"
        contract_present = "yes" if "договор" in lowered else "unknown"
        known_facts = []
        if "договор" in lowered:
            known_facts.append("Существует договор между сторонами.")
        if "не испол" in lowered:
            known_facts.append("Обязательство по договору не исполнено.")
        if "не возвращ" in lowered or "деньг" in lowered:
            known_facts.append("Пользователь указывает на невозврат денежных средств.")

        clarifying_questions = [
            "Какое именно обязательство по договору не было исполнено?",
            "Кто является второй стороной договора и какова сумма спора?",
        ]
        return {
            "summary": text,
            "preliminary_case_type": case_type,
            "confidence": "low",
            "parties": {
                "applicant_role": "unknown",
                "applicant_name": None,
                "opponent_role": opponent_role,
                "opponent_name": None,
            },
            "transaction": {
                "type": "service" if "услуг" in lowered else "unknown",
                "item_or_service": None,
                "price": FactExtractor._extract_amount(lowered),
                "currency": "RUB" if "руб" in lowered else "unknown",
                "purchase_or_order_date": {"exact_date": None, "relative_date": None, "raw_text": None},
                "purpose": "unknown",
            },
            "problem": {
                "problem_type": problem_type,
                "description": text,
                "problem_date": {"exact_date": None, "relative_date": None, "raw_text": None},
            },
            "user_demand": {
                "demand_type": demand_type,
                "description": "Возврат денег" if demand_type == "refund" else None,
                "amount": FactExtractor._extract_amount(lowered),
                "currency": "RUB" if "руб" in lowered else "unknown",
            },
            "prior_contact": {
                "contacted_opponent": "unknown",
                "contact_method": "unknown",
                "contact_date": {"exact_date": None, "relative_date": None, "raw_text": None},
                "opponent_response": None,
            },
            "documents": {
                "receipt": "unknown",
                "contract": contract_present,
                "warranty_card": "unknown",
                "photos_or_video": "unknown",
                "correspondence": "unknown",
                "other_documents": [],
            },
            "known_facts": known_facts,
            "uncertain_facts": [],
            "missing_fields": [
                {"field": "Сумма спора", "reason": "Нужна сумма, чтобы оценить требование пользователя."},
                {"field": "Предмет договора", "reason": "Нужно понимать, что именно должен был исполнить контрагент."},
            ],
            "clarifying_questions": clarifying_questions,
            "risk_flags": [{"flag": "llm_fallback", "reason": "Факты извлечены эвристически из-за недоступности LLM."}],
        }

    @classmethod
    def _postprocess_facts(cls, facts: dict[str, Any], user_text: str) -> dict[str, Any]:
        result = dict(facts) if isinstance(facts, dict) else {}
        result["normalized_claims"] = cls._normalize_claims(result, user_text)
        return result

    @classmethod
    def _normalize_claims(cls, facts: dict[str, Any], user_text: str) -> list[str]:
        normalized: list[str] = []
        text = " ".join(
            [
                str(user_text or ""),
                str(facts.get("summary") or ""),
                str(((facts.get("user_demand") or {}) if isinstance(facts.get("user_demand"), dict) else {}).get("demand_type") or ""),
                str(((facts.get("user_demand") or {}) if isinstance(facts.get("user_demand"), dict) else {}).get("description") or ""),
                str(((facts.get("problem") or {}) if isinstance(facts.get("problem"), dict) else {}).get("description") or ""),
            ]
        ).lower()

        user_demand = facts.get("user_demand") if isinstance(facts.get("user_demand"), dict) else {}
        demand_type = str(user_demand.get("demand_type") or "").lower()
        demand_description = str(user_demand.get("description") or "").lower()

        if demand_type == "refund" or any(token in text for token in ("возврат", "вернуть деньги", "возвратить деньги")):
            normalized.append("refund_principal")
        if any(token in text for token in ("процент", "ст. 395", "пользовани чуж", "неправомерн удержан")):
            normalized.append("interest")
        if demand_type == "compensation" or any(token in text for token in ("убыт", "компенсац", "возмест")):
            normalized.append("damages")
        if demand_type == "perform_service" or any(token in text for token in ("исполнить", "выполнить", "оказать услугу")):
            normalized.append("performance")
        if demand_type == "cancel_contract" or any(token in text for token in ("расторг", "отказ от договора", "отказаться от договора")):
            normalized.append("termination/refusal")
        if any(token in text for token in ("неустой", "штраф", "пен", "задат", "обеспеч")):
            normalized.append("penalty")
        if any(token in text for token in ("реституц", "неосновательн", "вернуть уплаченное", "возврат уплаченного")):
            normalized.append("restitution")

        if not normalized and (demand_type or demand_description):
            normalized.append("other")

        deduped: list[str] = []
        for claim in normalized:
            if claim not in deduped:
                deduped.append(claim)
        return deduped

    @staticmethod
    def _extract_amount(text: str) -> float | None:
        match = re.search(r"(\d+(?:[.,]\d+)?)", text)
        if not match:
            return None
        return float(match.group(1).replace(",", "."))
