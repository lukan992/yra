import json
import re
from typing import Any

from app.core.config import get_settings
from app.schemas.laws import LawQueryPayload
from app.schemas.pipeline import LLMError, QueryBuilderError
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
        prompt = "{template}\n\nUSER_TEXT:\n{user_text}\n\nFACTS:\n{facts}\n\nLEGAL_AREA:\n{legal_area}\n".format(
            template=prompt_template,
            user_text=json.dumps(user_text, ensure_ascii=False),
            facts=json.dumps(facts, ensure_ascii=False),
            legal_area=json.dumps(legal_area, ensure_ascii=False),
        )
        fallback_reason = ""
        try:
            raw_result = self.litellm_client.complete_json(
                prompt,
                self.settings.law_query_builder_model,
                stage="law_query_building",
            )
            result = self.ensure_query_payload(user_text, facts, legal_area, raw_result)
            fallback_reason = self.last_trace.get("fallback_reason", "")
            return result
        except LLMError:
            result = self.ensure_query_payload(user_text, facts, legal_area, None, fallback_reason="llm_error")
            return result
        except Exception:
            result = self.ensure_query_payload(user_text, facts, legal_area, None, fallback_reason="llm_exception")
            return result
        except QueryBuilderError:
            result = self.ensure_query_payload(user_text, facts, legal_area, None, fallback_reason=fallback_reason or "invalid_query_payload")
            return result

    def ensure_query_payload(
        self,
        user_text: str,
        facts: dict[str, Any],
        legal_area: dict[str, Any],
        candidate_payload: dict[str, Any] | None,
        fallback_reason: str = "",
    ) -> dict[str, Any]:
        normalized = self._normalize_payload(candidate_payload)
        reason = fallback_reason
        if not self._is_usable_payload(normalized):
            reason = reason or self._fallback_reason(normalized)
            normalized = self._fallback_query(user_text, facts, legal_area)
        if not self._is_usable_payload(normalized):
            raise QueryBuilderError(details={"query_payload": normalized})
        self.last_trace = self._build_trace(
            normalized,
            fallback_used=bool(normalized.get("fallback_used")),
            fallback_reason=reason if normalized.get("fallback_used") else "",
            user_text=user_text,
            facts=facts,
            legal_area=legal_area,
        )
        return normalized

    def _normalize_payload(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        model = LawQueryPayload.model_validate(payload)
        normalized = model.model_dump()
        if not normalized.get("plain_problem"):
            normalized["plain_problem"] = normalized.get("legal_query") or ""
        if not normalized.get("legal_query") and normalized.get("plain_problem"):
            normalized["legal_query"] = normalized["plain_problem"]
        if not normalized.get("keywords") and normalized.get("legal_query"):
            normalized["keywords"] = self._keywords_from_text(normalized["legal_query"])
        return normalized

    def _fallback_query(self, user_text: str, facts: dict[str, Any], legal_area: dict[str, Any]) -> dict[str, Any]:
        summary = " ".join(str(facts.get("summary") or user_text or "").split()).strip()
        detected_claims = self._string_list(legal_area.get("detected_claims") or facts.get("normalized_claims"))
        domain_signals = self._string_list(legal_area.get("domain_signals"))
        scenario = self._detect_scenario(user_text, facts, legal_area)

        if scenario == "contract_services_nonperformance_refund":
            keywords = [
                "договор оказания услуг",
                "неисполнение обязательства",
                "просрочка исполнения",
                "возврат оплаты",
                "возмещение убытков",
                "отказ вернуть деньги",
            ]
            legal_query = "неисполнение договора оказания услуг возврат оплаты возмещение убытков просрочка исполнения"
        else:
            keywords = self._fallback_keywords(user_text, facts, legal_area)
            legal_query = " ".join(dict.fromkeys([*detected_claims, *domain_signals, *keywords])).strip() or summary

        expected_acts = self._expected_acts(facts, legal_area, scenario)
        queries = self._build_queries(detected_claims, keywords)
        search_notes = self._build_search_notes(detected_claims, domain_signals)
        return {
            "plain_problem": summary or legal_query,
            "legal_query": legal_query or summary,
            "queries": queries,
            "keywords": keywords,
            "expected_acts": expected_acts,
            "expected_act_types": [],
            "detected_claims": detected_claims,
            "search_notes": search_notes,
            "fallback_used": True,
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
        fallback_reason: str,
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
            "fallback_reason": fallback_reason or None,
            "detected_scenario": cls._detect_scenario(user_text, facts, legal_area),
        }

    @staticmethod
    def _is_usable_payload(payload: dict[str, Any]) -> bool:
        if not payload:
            return False
        legal_query = str(payload.get("legal_query") or "").strip()
        keywords = payload.get("keywords") if isinstance(payload.get("keywords"), list) else []
        return bool(legal_query and keywords)

    @staticmethod
    def _fallback_reason(payload: dict[str, Any]) -> str:
        if not payload:
            return "empty_payload"
        if not any(payload.get(key) for key in ("plain_problem", "legal_query", "keywords", "expected_acts", "detected_claims")):
            return "empty_payload"
        if not str(payload.get("legal_query") or "").strip():
            return "missing_legal_query"
        if not isinstance(payload.get("keywords"), list) or not payload.get("keywords"):
            return "missing_keywords"
        return "invalid_schema"

    def _fallback_keywords(self, user_text: str, facts: dict[str, Any], legal_area: dict[str, Any]) -> list[str]:
        lowered = self._source_text(user_text, facts, legal_area).lower()
        keywords: list[str] = []
        keyword_map = [
            ("договор", "договор"),
            ("услуг", "договор оказания услуг"),
            ("неисполн", "неисполнение обязательства"),
            ("просроч", "просрочка исполнения"),
            ("возврат", "возврат оплаты"),
            ("деньг", "отказ вернуть деньги"),
            ("убыт", "возмещение убытков"),
            ("оплат", "оплата по договору"),
        ]
        for token, keyword in keyword_map:
            if token in lowered and keyword not in keywords:
                keywords.append(keyword)
        if not keywords:
            keywords = self._keywords_from_text(str(facts.get("summary") or user_text))
        return keywords or ["договор", "неисполнение обязательства"]

    @staticmethod
    def _keywords_from_text(text: str) -> list[str]:
        raw = re.findall(r"[A-Za-zА-Яа-яЁё0-9]{4,}", text.lower())
        result: list[str] = []
        for item in raw:
            if item not in result:
                result.append(item)
            if len(result) >= 6:
                break
        return result

    def _expected_acts(self, facts: dict[str, Any], legal_area: dict[str, Any], scenario: str) -> list[str]:
        acts: list[str] = []
        primary_area = str(legal_area.get("primary_area") or "").lower()
        secondary_areas = {str(item).lower() for item in self._string_list(legal_area.get("secondary_areas"))}
        lowered = self._source_text("", facts, legal_area).lower()
        if primary_area in {"civil", "business", "general", "consumer"} or scenario == "contract_services_nonperformance_refund" or any(
            token in lowered for token in ("договор", "услуг", "неисполн", "возврат", "убыт")
        ):
            acts.append("ГК РФ")
        if primary_area == "consumer" or "consumer" in secondary_areas:
            acts.append("Закон о защите прав потребителей")
        return list(dict.fromkeys(acts))

    @staticmethod
    def _build_queries(detected_claims: list[str], keywords: list[str]) -> list[dict[str, Any]]:
        purpose_map = {
            "refund_principal": ("refund_principal", "Найти нормы о возврате уплаченной суммы по договору."),
            "damages": ("damages", "Найти нормы о возмещении убытков при нарушении обязательства."),
            "interest": ("interest_recovery", "Найти нормы о процентах за удержание денежных средств."),
            "performance": ("performance_terms", "Найти нормы о сроке и надлежащем исполнении обязательства."),
            "termination/refusal": ("termination_or_refusal", "Найти нормы о расторжении или отказе от договора."),
        }
        queries: list[dict[str, Any]] = []
        for claim in detected_claims:
            purpose, effect = purpose_map.get(claim, ("other", "Найти нормы по заявленному требованию."))
            queries.append(
                {
                    "purpose": purpose,
                    "query": " ".join(dict.fromkeys([claim.replace("_", " "), *keywords[:4]])).strip(),
                    "desired_legal_effect": effect,
                    "keywords": keywords[:4],
                }
            )
        return queries

    @staticmethod
    def _build_search_notes(detected_claims: list[str], domain_signals: list[str]) -> list[str]:
        notes: list[str] = []
        if detected_claims:
            notes.append("Покрыть все detected claims через нормы с прямым правовым эффектом.")
        if domain_signals:
            notes.append("Учитывать договор, оплату и неисполнение как основные сигналы поиска.")
        return notes

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            normalized = " ".join(str(item or "").split()).strip()
            if normalized:
                result.append(normalized)
        return result

    @staticmethod
    def _source_text(user_text: str, facts: dict[str, Any], legal_area: dict[str, Any]) -> str:
        parts = [str(user_text or ""), str(facts.get("summary") or "")]
        transaction = facts.get("transaction") if isinstance(facts.get("transaction"), dict) else {}
        problem = facts.get("problem") if isinstance(facts.get("problem"), dict) else {}
        demand = facts.get("demand") if isinstance(facts.get("demand"), dict) else {}
        parts.extend(str(value) for value in transaction.values() if value)
        parts.extend(str(value) for value in problem.values() if value)
        parts.extend(str(value) for value in demand.values() if value)
        parts.extend(LawQueryBuilder._string_list(legal_area.get("domain_signals")))
        return " ".join(parts)
