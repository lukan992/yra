import json
from typing import Any

from app.core.config import get_settings
from app.schemas.pipeline import LLMError
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class LegalGuidanceGenerator:
    DIRECT_COVERAGE_TYPES = {"direct", "valid_conditional"}

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
            result = self.llm_client.complete_json(
                prompt,
                self.settings.guidance_generation_model,
                stage="guidance_generation",
            )
        except LLMError:
            result = self._fallback_guidance(facts, legal_context, legal_area or {})
        return self._postprocess_guidance(result, facts, legal_context, legal_area or {})

    @classmethod
    def _fallback_guidance(
        cls,
        facts: dict[str, Any], legal_context: list[dict[str, Any]], legal_area: dict[str, Any]
    ) -> dict[str, Any]:
        applicable_laws = cls._build_applicable_laws(legal_context)
        rights_by_claim = cls._build_rights_by_claim(facts, legal_context)
        return {
            "document_type": "legal_guidance",
            "status": "legal_guidance",
            "legal_domain": legal_area.get("primary_area") or "unknown",
            "case_type": facts.get("preliminary_case_type") or "unknown",
            "summary": facts.get("summary") or "Требуется дополнительная правовая оценка.",
            "applicable_laws": applicable_laws,
            "rights_by_claim": rights_by_claim,
            "rights": cls._build_rights(rights_by_claim, applicable_laws, facts),
            "recommended_actions": cls._build_recommended_actions(facts, rights_by_claim),
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

        rights_by_claim = cls._normalize_rights_by_claim(result.get("rights_by_claim"), legal_context, facts)
        if not rights_by_claim:
            rights_by_claim = cls._build_rights_by_claim(facts, legal_context)
        result["rights_by_claim"] = rights_by_claim

        rights = result.get("rights") if isinstance(result.get("rights"), list) else []
        normalized_rights = cls._normalize_rights(rights, rights_by_claim, applicable_laws, facts)
        if not normalized_rights:
            normalized_rights = cls._build_rights(rights_by_claim, applicable_laws, facts)
        result["rights"] = normalized_rights

        recommended_actions = result.get("recommended_actions") if isinstance(result.get("recommended_actions"), list) else []
        result["recommended_actions"] = cls._normalize_recommended_actions(recommended_actions, facts, rights_by_claim)

        questions = result.get("clarifying_questions") if isinstance(result.get("clarifying_questions"), list) else []
        result["clarifying_questions"] = cls._normalize_questions(questions, facts)

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
            for item in legal_context
            if (item.get("act_name") or item.get("law_name") or item.get("article_number"))
            and LegalGuidanceGenerator._is_user_visible_article(item)
        ][:5]

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
            and cls._is_user_visible_article(item)
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
    def _build_rights(
        cls,
        rights_by_claim: list[dict[str, Any]],
        applicable_laws: list[dict[str, Any]],
        facts: dict[str, Any],
    ) -> list[str]:
        rights: list[str] = []
        has_missing_damages_details = cls._has_missing_damages_details(facts)
        for item in rights_by_claim:
            claim = str(item.get("claim") or "")
            status = str(item.get("status") or "")
            if claim == "refund_principal":
                if status == "covered":
                    rights.append("По возврату оплаты уже есть нормы, на которые можно опираться при требовании вернуть уплаченную сумму")
                elif status == "blocked_by_missing_facts":
                    rights.append("По возврату оплаты найденные нормы зависят от дополнительных условий, которые пока не подтверждены фактами")
                else:
                    rights.append("По возврату оплаты прямое основание среди найденных норм пока не подтверждено, поэтому может потребоваться дополнительный подбор норм или уточнение основания отказа от договора")
            elif claim == "damages":
                if status == "covered" and has_missing_damages_details:
                    rights.append("Требование о возмещении убытков в целом поддерживается найденными нормами, но для его расчета и обоснования нужно уточнить состав, размер и доказательства убытков")
                elif status == "covered":
                    rights.append("По убыткам есть прямые нормы, которые позволяют заявлять требование о возмещении вреда от нарушения обязательства")
                elif status == "blocked_by_missing_facts":
                    rights.append("По убыткам применимость найденных норм зависит от дополнительных условий, которые пока не подтверждены фактами")
            elif claim == "interest" and status == "covered":
                rights.append("Проценты можно рассматривать отдельно, если подтвердится неправомерное удержание денежных средств")
            elif claim == "termination/refusal" and status == "covered":
                rights.append("Есть нормы, которые поддерживают отказ от договора или его расторжение при нарушении обязательства")

        if not rights and applicable_laws:
            law = applicable_laws[0]
            law_name = str(law.get("law_name") or "").strip()
            article_number = str(law.get("article_number") or "").strip()
            why_relevant = str(law.get("why_relevant") or "").strip()
            if law_name and article_number:
                suffix = f"Согласно {law_name}, ст. {article_number}"
                if why_relevant:
                    rights.append(f"Пользователь вправе ссылаться на найденную норму. {suffix}. Эта статья применима, потому что {why_relevant}")
                else:
                    rights.append(f"Пользователь вправе ссылаться на найденную норму. {suffix}")
        return rights

    @classmethod
    def _normalize_rights(
        cls,
        rights: list[Any],
        rights_by_claim: list[dict[str, Any]],
        applicable_laws: list[dict[str, Any]],
        facts: dict[str, Any],
    ) -> list[str]:
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
        if normalized:
            return normalized
        return cls._build_rights(rights_by_claim, applicable_laws, facts)

    @classmethod
    def _build_rights_by_claim(cls, facts: dict[str, Any], legal_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        claim_entries: dict[str, list[dict[str, Any]]] = {}
        for article in legal_context:
            article_coverage = article.get("coverage") if isinstance(article.get("coverage"), list) else []
            for entry in article_coverage:
                if not isinstance(entry, dict):
                    continue
                claim = str(entry.get("claim") or "").strip()
                if not claim:
                    continue
                claim_entries.setdefault(claim, []).append(entry)

        claims = facts.get("normalized_claims") if isinstance(facts.get("normalized_claims"), list) else list(claim_entries.keys())
        result: list[dict[str, Any]] = []
        for claim in claims:
            if not isinstance(claim, str):
                continue
            entries = claim_entries.get(claim, [])
            direct_entries = [entry for entry in entries if entry.get("coverage_type") in cls.DIRECT_COVERAGE_TYPES]
            blocked_entries = [entry for entry in entries if entry.get("coverage_type") == "conditional_missing_facts"]
            status = "covered" if direct_entries else "blocked_by_missing_facts" if blocked_entries else "missing"
            legal_bases: list[dict[str, Any]] = []
            for article in legal_context:
                article_coverage = article.get("coverage") if isinstance(article.get("coverage"), list) else []
                for entry in article_coverage:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("claim") != claim or entry.get("coverage_type") not in cls.DIRECT_COVERAGE_TYPES:
                        continue
                    legal_bases.append(
                        {
                            "act_name": article.get("act_name") or article.get("law_name"),
                            "article_number": article.get("article_number"),
                            "article_title": article.get("article_title"),
                            "why_relevant": str(entry.get("effect_description") or "").strip() or str(article.get("why_relevant") or "").strip(),
                            "condition": ", ".join(entry.get("trigger_conditions") or []) if entry.get("coverage_type") == "valid_conditional" else None,
                        }
                    )
            legal_bases = legal_bases[:3]
            missing_facts = sorted(
                {
                    str(fact)
                    for entry in blocked_entries
                    for fact in (
                        entry.get("missing_conditions")
                        if isinstance(entry.get("missing_conditions"), list)
                        else entry.get("missing_facts")
                        if isinstance(entry.get("missing_facts"), list)
                        else []
                    )
                    if fact
                }
            )
            warning = None
            if claim == "damages" and cls._has_missing_damages_details(facts):
                warning = "Для убытков нужно уточнить состав, размер и подтверждающие документы."
            result.append(
                {
                    "claim": claim,
                    "status": status,
                    "plain_explanation": cls._plain_claim_explanation(claim, status, facts),
                    "legal_bases": legal_bases,
                    "missing_facts": missing_facts,
                    "warning": warning,
                }
            )
        return result

    @classmethod
    def _normalize_rights_by_claim(
        cls,
        raw_claims: Any,
        legal_context: list[dict[str, Any]],
        facts: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_claims, list):
            return []
        allowed_articles = {
            (str(item.get("act_name") or item.get("law_name") or "").strip().casefold(), str(item.get("article_number") or "").strip())
            for item in legal_context
            if cls._is_user_visible_article(item)
        }
        normalized: list[dict[str, Any]] = []
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            legal_bases: list[dict[str, Any]] = []
            raw_bases = item.get("legal_bases") if isinstance(item.get("legal_bases"), list) else []
            for basis in raw_bases:
                if not isinstance(basis, dict):
                    continue
                key = (
                    str(basis.get("act_name") or basis.get("law_name") or "").strip().casefold(),
                    str(basis.get("article_number") or "").strip(),
                )
                if allowed_articles and key not in allowed_articles:
                    continue
                legal_bases.append(
                    {
                        "act_name": basis.get("act_name") or basis.get("law_name"),
                        "article_number": basis.get("article_number"),
                        "article_title": basis.get("article_title"),
                        "why_relevant": basis.get("why_relevant"),
                        "condition": basis.get("condition"),
                    }
                )
            normalized.append(
                {
                    "claim": item.get("claim"),
                    "status": item.get("status"),
                    "plain_explanation": item.get("plain_explanation") or cls._plain_claim_explanation(str(item.get("claim") or ""), str(item.get("status") or ""), facts),
                    "legal_bases": legal_bases,
                    "missing_facts": item.get("missing_facts") if isinstance(item.get("missing_facts"), list) else [],
                    "warning": item.get("warning"),
                }
            )
        return normalized

    @classmethod
    def _build_recommended_actions(cls, facts: dict[str, Any], rights_by_claim: list[dict[str, Any]]) -> list[str]:
        actions = ["Соберите договор, переписку и документы, подтверждающие неисполнение обязательства."]
        refund_entry = next((item for item in rights_by_claim if item.get("claim") == "refund_principal"), None)
        damages_entry = next((item for item in rights_by_claim if item.get("claim") == "damages"), None)
        if refund_entry and refund_entry.get("status") == "covered":
            actions.append("Можно готовить требование о возврате уплаченной суммы и фиксировать отказ исполнителя.")
        if damages_entry and cls._has_missing_damages_details(facts):
            actions.append("Для убытков уточните их состав, размер и документы, которыми они подтверждаются.")
        return cls._dedupe_strings(actions)

    @classmethod
    def _normalize_recommended_actions(
        cls,
        actions: list[Any],
        facts: dict[str, Any],
        rights_by_claim: list[dict[str, Any]],
    ) -> list[str]:
        normalized = cls._dedupe_strings(str(item).strip() for item in actions if str(item).strip())
        if cls._has_missing_damages_details(facts) and not any("убыт" in item.lower() for item in normalized):
            normalized.append("Для убытков уточните их состав, размер и документы, которыми они подтверждаются.")
        if not normalized:
            return cls._build_recommended_actions(facts, rights_by_claim)
        return normalized

    @classmethod
    def _normalize_questions(cls, questions: list[Any], facts: dict[str, Any]) -> list[str]:
        normalized = cls._dedupe_strings(str(item).strip() for item in questions if str(item).strip())
        if cls._has_missing_damages_details(facts):
            damages_question = "Какие именно убытки, кроме суммы оплаты, вы понесли и чем они подтверждаются?"
            if damages_question not in normalized:
                normalized.append(damages_question)
        return normalized[:3]

    @staticmethod
    def _plain_claim_explanation(claim: str, status: str, facts: dict[str, Any]) -> str:
        if claim == "refund_principal":
            if status == "covered":
                return "По возврату оплаты найдено прямое правовое основание."
            if status == "blocked_by_missing_facts":
                return "По возврату оплаты нужны дополнительные факты для применения найденной нормы."
            return "По возврату оплаты прямое основание среди найденных норм пока не подтверждено."
        if claim == "damages":
            if status == "covered" and LegalGuidanceGenerator._has_missing_damages_details(facts):
                return "По убыткам есть подходящие нормы, но для отдельного требования нужно уточнить состав и размер убытков."
            if status == "covered":
                return "По убыткам найдено прямое правовое основание."
            if status == "blocked_by_missing_facts":
                return "По убыткам применимость найденной нормы зависит от дополнительных условий."
            return "По убыткам прямое основание среди найденных норм пока не подтверждено."
        return "Требование требует отдельной правовой оценки."

    @staticmethod
    def _has_missing_damages_details(facts: dict[str, Any]) -> bool:
        missing_fields = facts.get("missing_fields") if isinstance(facts.get("missing_fields"), list) else []
        for item in missing_fields:
            if isinstance(item, dict):
                haystack = " ".join(str(value) for value in item.values()).lower()
            else:
                haystack = str(item).lower()
            if "убыт" in haystack:
                return True
        return False

    @classmethod
    def _is_user_visible_article(cls, article: dict[str, Any]) -> bool:
        coverage_type = str(article.get("coverage_type") or "").strip()
        return not coverage_type or coverage_type in cls.DIRECT_COVERAGE_TYPES

    @staticmethod
    def _dedupe_strings(values: Any) -> list[str]:
        result: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            cleaned = value.strip()
            if cleaned and cleaned not in result:
                result.append(cleaned)
        return result
