import json
from typing import Any

from app.core.config import get_settings
from app.schemas.pipeline import LLMError
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class LegalGuidanceGenerator:
    def __init__(self, llm_client: LiteLLMClient, prompt_loader: PromptLoader) -> None:
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader
        self.settings = get_settings()

    def generate(
        self,
        user_text: str,
        facts: dict[str, Any],
        legal_context: list[dict[str, Any]],
        legal_area: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prompt_template = self.prompt_loader.load("legal_guidance_generator.md")
        prompt = (
            prompt_template.replace("{{USER_TEXT}}", json.dumps(user_text, ensure_ascii=False))
            .replace("{{FACTS}}", json.dumps(facts, ensure_ascii=False))
            .replace("{{LEGAL_AREA}}", json.dumps(legal_area or {}, ensure_ascii=False))
            .replace("{{LEGAL_CONTEXT}}", json.dumps(legal_context, ensure_ascii=False))
        )
        try:
            result = self.llm_client.complete_json(prompt, self.settings.litellm_main_model)
        except LLMError:
            result = self._fallback_guidance(facts, legal_context, legal_area or {})
        return self._postprocess_guidance(result, facts, legal_context, legal_area or {})

    @classmethod
    def _fallback_guidance(
        cls,
        facts: dict[str, Any], legal_context: list[dict[str, Any]], legal_area: dict[str, Any]
    ) -> dict[str, Any]:
        applicable_laws = cls._build_applicable_laws(legal_context)
        return {
            "document_type": "legal_guidance",
            "status": "legal_guidance",
            "legal_domain": legal_area.get("primary_area") or "unknown",
            "case_type": facts.get("preliminary_case_type") or "unknown",
            "summary": facts.get("summary") or "Требуется дополнительная правовая оценка.",
            "applicable_laws": applicable_laws,
            "rights": cls._build_rights(applicable_laws, facts),
            "recommended_actions": ["Соберите договор, переписку и документы, подтверждающие неисполнение обязательства."],
            "risks": ["Fallback guidance was used because the LLM guidance generator was unavailable."],
            "missing_fields": facts.get("missing_fields") if isinstance(facts.get("missing_fields"), list) else [],
            "clarifying_questions": facts.get("clarifying_questions") if isinstance(facts.get("clarifying_questions"), list) else [],
        }

    @classmethod
    def _postprocess_guidance(
        cls,
        guidance: dict[str, Any],
        facts: dict[str, Any],
        legal_context: list[dict[str, Any]],
        legal_area: dict[str, Any],
    ) -> dict[str, Any]:
        result = dict(guidance) if isinstance(guidance, dict) else {}
        result.setdefault("document_type", "legal_guidance")
        result.setdefault("status", "legal_guidance")
        result.setdefault("legal_domain", legal_area.get("primary_area") or "unknown")
        result.setdefault("case_type", facts.get("preliminary_case_type") or "unknown")
        result.setdefault("summary", facts.get("summary") or "Требуется дополнительная правовая оценка.")

        applicable_laws = cls._normalize_applicable_laws(result.get("applicable_laws"), legal_context)
        if not applicable_laws and legal_context:
            applicable_laws = cls._build_applicable_laws(legal_context)
        result["applicable_laws"] = applicable_laws

        rights = result.get("rights") if isinstance(result.get("rights"), list) else []
        normalized_rights = cls._normalize_rights(rights, applicable_laws)
        if not normalized_rights and applicable_laws:
            normalized_rights = cls._build_rights(applicable_laws, facts)
        result["rights"] = normalized_rights

        return result

    @staticmethod
    def _build_applicable_laws(legal_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "law_id": str(item.get("id") or f"{item.get('act_name') or item.get('law_name')}-{item.get('article_number') or 'unknown'}"),
                "law_name": item.get("act_name") or item.get("law_name"),
                "article_number": item.get("article_number"),
                "article_title": item.get("article_title"),
                "why_relevant": item.get("why_relevant") or "Статья включена в подтвержденный правовой контекст.",
            }
            for item in legal_context[:5]
            if item.get("act_name") or item.get("law_name") or item.get("article_number")
        ]

    @classmethod
    def _normalize_applicable_laws(
        cls, raw_laws: Any, legal_context: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        context_by_key = {
            (
                str(item.get("act_name") or item.get("law_name") or "").strip().casefold(),
                str(item.get("article_number") or "").strip(),
            ): item
            for item in legal_context
            if (item.get("act_name") or item.get("law_name")) and item.get("article_number")
        }
        result: list[dict[str, Any]] = []
        for item in raw_laws if isinstance(raw_laws, list) else []:
            if not isinstance(item, dict):
                continue
            law_name = str(item.get("law_name") or item.get("act_name") or "").strip()
            article_number = str(item.get("article_number") or "").strip()
            if not law_name or not article_number:
                continue
            context_match = context_by_key.get((law_name.casefold(), article_number), {})
            if context_by_key and not context_match:
                continue
            result.append(
                {
                    "law_id": str(item.get("law_id") or context_match.get("id") or f"{law_name}-{article_number}"),
                    "law_name": law_name,
                    "article_number": article_number,
                    "article_title": item.get("article_title") or context_match.get("article_title"),
                    "why_relevant": item.get("why_relevant") or context_match.get("why_relevant") or "Статья включена в подтвержденный правовой контекст.",
                }
            )
        return result[:5]

    @classmethod
    def _build_rights(cls, applicable_laws: list[dict[str, Any]], facts: dict[str, Any]) -> list[str]:
        case_summary = str(facts.get("summary") or facts.get("problem_summary") or "").strip()
        rights: list[str] = []
        for law in applicable_laws[:3]:
            law_name = str(law.get("law_name") or "").strip()
            article_number = str(law.get("article_number") or "").strip()
            article_title = str(law.get("article_title") or "").strip()
            why_relevant = str(law.get("why_relevant") or "").strip()
            if not law_name or not article_number:
                continue
            basis = f"Согласно {law_name}, ст. {article_number}"
            if article_title:
                basis = f"{basis} ({article_title})"
            rationale = why_relevant or "Статья относится к подтвержденному правовому контексту по этой ситуации."
            if case_summary:
                rights.append(
                    f"Пользователь вправе требовать защиту нарушенного права по описанной ситуации. {basis}. "
                    f"Эта статья применима, потому что {rationale}"
                )
            else:
                rights.append(
                    f"Пользователь вправе ссылаться на подтвержденную норму права. {basis}. "
                    f"Эта статья применима, потому что {rationale}"
                )
        return rights

    @classmethod
    def _normalize_rights(cls, rights: list[Any], applicable_laws: list[dict[str, Any]]) -> list[str]:
        normalized: list[str] = []
        for index, item in enumerate(rights):
            text = str(item).strip()
            if not text:
                continue
            if "ст." in text.lower():
                normalized.append(text)
                continue
            law = applicable_laws[index] if index < len(applicable_laws) else (applicable_laws[0] if applicable_laws else None)
            if law:
                law_name = str(law.get("law_name") or "").strip()
                article_number = str(law.get("article_number") or "").strip()
                why_relevant = str(law.get("why_relevant") or "").strip()
                if law_name and article_number:
                    suffix = f"Согласно {law_name}, ст. {article_number}."
                    if why_relevant:
                        suffix = f"{suffix} Эта статья применима, потому что {why_relevant}"
                    normalized.append(f"{text}. {suffix}")
                    continue
            normalized.append(text)
        return normalized
