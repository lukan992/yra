import json
from typing import Any

from app.core.config import get_settings
from app.schemas.pipeline import LLMError
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class LegalContextValidator:
    def __init__(self, llm_client: LiteLLMClient, prompt_loader: PromptLoader) -> None:
        self.litellm_client = llm_client
        self.prompt_loader = prompt_loader
        self.settings = get_settings()
        self.last_trace: dict[str, Any] = {}

    def validate(
        self,
        user_text: str,
        facts: dict[str, Any],
        legal_area: dict[str, Any],
        legal_context: list[dict[str, Any]],
    ) -> dict[str, Any]:
        deterministic = self._validate_coverage_map(facts, legal_context)
        if deterministic:
            self.last_trace = self._build_trace(legal_context, deterministic)
            return deterministic

        prompt_template = self.prompt_loader.load("legal_context_validator.md")
        prompt = (
            prompt_template.replace("{{USER_TEXT}}", json.dumps(user_text, ensure_ascii=False))
            .replace("{{FACTS}}", json.dumps(facts, ensure_ascii=False))
            .replace("{{LEGAL_AREA}}", json.dumps(legal_area, ensure_ascii=False))
            .replace("{{LEGAL_CONTEXT}}", json.dumps(legal_context, ensure_ascii=False))
        )
        try:
            result = self.litellm_client.complete_json(prompt, self.settings.litellm_main_model)
        except LLMError:
            result = self._fallback_validation(legal_context)
        self.last_trace = self._build_trace(legal_context, result)
        return result

    @staticmethod
    def _validate_coverage_map(facts: dict[str, Any], legal_context: list[dict[str, Any]]) -> dict[str, Any] | None:
        normalized_claims = facts.get("normalized_claims") if isinstance(facts.get("normalized_claims"), list) else []
        entries: list[dict[str, Any]] = []
        for article in legal_context:
            article_entries = article.get("coverage") if isinstance(article.get("coverage"), list) else []
            entries.extend(entry for entry in article_entries if isinstance(entry, dict))
        if not entries:
            return None

        valid_claims = {
            str(entry.get("claim"))
            for entry in entries
            if entry.get("counts_as_covered") and isinstance(entry.get("claim"), str)
        }
        if not normalized_claims:
            normalized_claims = sorted(valid_claims)
        missing_claims = [claim for claim in normalized_claims if claim not in valid_claims]
        blocked_by_missing_facts = [
            {
                "claim": entry.get("claim"),
                "article_number": entry.get("article_number"),
                "missing_facts": entry.get("missing_facts"),
            }
            for entry in entries
            if entry.get("coverage_type") == "conditional_missing_facts" and entry.get("missing_facts")
        ]
        has_direct_basis = any(entry.get("coverage_type") in {"direct", "valid_conditional"} for entry in entries if entry.get("counts_as_covered"))

        if not legal_context or (normalized_claims and not valid_claims):
            status = "insufficient_context"
        elif missing_claims and blocked_by_missing_facts:
            status = "blocked_by_missing_facts"
        elif missing_claims:
            status = "partial"
        elif valid_claims:
            status = "covered"
        else:
            status = "no_coverage"

        warnings: list[str] = []
        if missing_claims:
            warnings.append("Не все заявленные требования подтверждены нормами с выполненными условиями применимости.")
        if blocked_by_missing_facts:
            warnings.append("Часть норм содержит условия, которые не подтверждены извлеченными фактами.")
        return {
            "status": "ok" if status == "covered" else status,
            "coverage_status": status,
            "confidence": 0.9 if status == "covered" else 0.55 if status == "partial" else 0.25,
            "has_direct_basis": has_direct_basis,
            "needs_clarification": bool(missing_claims or blocked_by_missing_facts or status in {"insufficient_context", "no_coverage"}),
            "covered_claims": sorted(valid_claims),
            "missing_claims": missing_claims,
            "blocked_by_missing_facts": blocked_by_missing_facts,
            "missing_facts": [claim for claim in missing_claims] + [
                str(fact)
                for item in blocked_by_missing_facts
                for fact in (item.get("missing_facts") if isinstance(item.get("missing_facts"), list) else [])
            ],
            "warnings": warnings,
        }

    @staticmethod
    def _fallback_validation(legal_context: list[dict[str, Any]]) -> dict[str, Any]:
        if not legal_context:
            return {
                "status": "insufficient_context",
                "confidence": 0.0,
                "has_direct_basis": False,
                "needs_clarification": True,
                "missing_facts": ["legal_context"],
                "warnings": ["Fallback validator: legal context is empty."],
            }
        direct_norms = [item for item in legal_context if item.get("applicability") == "direct" and item.get("is_active", True)]
        if direct_norms:
            return {
                "status": "ok",
                "confidence": max(float(item.get("relevance_score") or 0.0) for item in direct_norms),
                "has_direct_basis": True,
                "needs_clarification": False,
                "missing_facts": [],
                "warnings": ["Fallback validator used because the LLM validator was unavailable."],
            }
        return {
            "status": "needs_clarification",
            "confidence": max(float(item.get("relevance_score") or 0.0) for item in legal_context),
            "has_direct_basis": False,
            "needs_clarification": True,
            "missing_facts": ["Нужны дополнительные факты для подтверждения прямой нормы."],
            "warnings": ["Fallback validator used because the LLM validator was unavailable."],
        }

    @classmethod
    def _build_trace(cls, legal_context: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for article in legal_context:
            applicability = str(article.get("applicability") or "")
            is_active = bool(article.get("is_active", True))
            summary = cls._summarize_article(article)
            rejection_reasons: list[str] = []
            if not is_active:
                rejection_reasons.append("inactive_article")
            if applicability == "not_applicable":
                rejection_reasons.append("applicability_not_applicable")
            if rejection_reasons:
                rejected.append({**summary, "reasons": rejection_reasons})
            else:
                accepted.append(summary)

        return {
            "articles_in": [cls._summarize_article(article) for article in legal_context],
            "accepted_articles": accepted,
            "rejected_articles": rejected,
            "confidence": float(result.get("confidence") or 0.0),
            "rejection_reasons": result.get("warnings") if isinstance(result.get("warnings"), list) else [],
            "status": result.get("status"),
            "legal_context": accepted,
        }

    @staticmethod
    def _summarize_article(article: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(article.get("id") or ""),
            "act_name": article.get("act_name") or article.get("law_name"),
            "article_number": article.get("article_number"),
            "title": article.get("article_title"),
            "applicability": article.get("applicability"),
            "relevance_score": float(article.get("relevance_score") or 0.0),
        }
