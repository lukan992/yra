import json
from typing import Any

from pydantic import ValidationError

from app.core.logging import log_json
from app.schemas.legal_rag import ClaimMatcherOutputSchema
from app.schemas.pipeline import LLMError
from app.services.coverage_gate import CoverageGate
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class ClaimMatcherLLM:
    CLAIM_EFFECTS = CoverageGate.CLAIM_EFFECTS
    SUPPORTING_EFFECTS = CoverageGate.SUPPORTING_EFFECTS

    def __init__(self, llm_client: LiteLLMClient | None = None, prompt_loader: PromptLoader | None = None) -> None:
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader

    def match(
        self,
        facts: dict[str, Any],
        normalized_claims: list[str],
        article_semantics: dict[str, Any],
        article: dict[str, Any],
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        llm_result = self._match_with_llm(
            facts,
            normalized_claims,
            article_semantics,
            article,
            request_id=request_id,
            run_id=run_id,
        )
        if llm_result:
            return llm_result
        return self._fallback_match(facts, normalized_claims, article_semantics, article)

    def _match_with_llm(
        self,
        facts: dict[str, Any],
        normalized_claims: list[str],
        article_semantics: dict[str, Any],
        article: dict[str, Any],
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.llm_client or not self.prompt_loader:
            return None
        settings = getattr(self.llm_client, "settings", None)
        model_name = getattr(settings, "claim_matcher_model", "") or "openai/gpt-4o-mini"
        system_prompt = self.prompt_loader.load("legal/claim_matcher_system.md")
        prompt = json.dumps(
            {
                "facts": facts,
                "normalized_claims": normalized_claims,
                "article_semantics": article_semantics,
                "article_metadata": {
                    "act_name": article.get("act_name"),
                    "article_number": article.get("article_number"),
                    "article_title": article.get("article_title") or article.get("title"),
                },
            },
            ensure_ascii=False,
        )
        try:
            try:
                raw = self.llm_client.complete_json(
                    prompt,
                    model_name,
                    system_prompt=system_prompt,
                    stage="claim_matcher",
                )
            except TypeError:
                raw = self.llm_client.complete_json(prompt, model_name)
            model = ClaimMatcherOutputSchema.model_validate(raw)
        except (FileNotFoundError, LLMError, ValidationError):
            return None
        result = model.model_dump()
        log_json(
            "legal_rag.claim_matcher.llm_output",
            request_id=request_id,
            run_id=run_id,
            step="law_reranking",
            article_number=result.get("article_number"),
            article_title=result.get("article_title"),
            payload=result,
        )
        return result

    def _fallback_match(
        self,
        facts: dict[str, Any],
        normalized_claims: list[str],
        article_semantics: dict[str, Any],
        article: dict[str, Any],
    ) -> dict[str, Any]:
        effects = article_semantics.get("legal_effects") if isinstance(article_semantics.get("legal_effects"), list) else []
        claim_matches: list[dict[str, Any]] = []
        for claim in normalized_claims:
            desired = self.CLAIM_EFFECTS.get(claim, set())
            supporting = self.SUPPORTING_EFFECTS.get(claim, set())
            chosen = next(
                (
                    effect
                    for effect in effects
                    if isinstance(effect, dict) and str(effect.get("effect_type") or "") in desired
                ),
                None,
            )
            proposed = "no_coverage"
            condition_status = "not_applicable"
            missing_conditions: list[str] = []
            matched_facts: list[str] = []
            reason = "no_semantic_effect_match_for_claim"
            if chosen:
                trigger_conditions = chosen.get("trigger_conditions") if isinstance(chosen.get("trigger_conditions"), list) else []
                missing_conditions = [condition for condition in trigger_conditions if not CoverageGate.condition_met(str(condition), facts, "")]
                matched_facts = [condition for condition in trigger_conditions if condition not in missing_conditions]
                condition_status = "missing_conditions" if missing_conditions else "satisfied"
                if chosen.get("effect_scope") == "special_conditional":
                    proposed = "conditional_missing_facts" if missing_conditions else "valid_conditional"
                else:
                    proposed = "direct" if not missing_conditions else "conditional_missing_facts"
                reason = "fallback_semantic_effect_matches_claim"
            else:
                chosen = next(
                    (
                        effect
                        for effect in effects
                        if isinstance(effect, dict) and str(effect.get("effect_type") or "") in supporting
                    ),
                    None,
                )
                if chosen:
                    proposed = "supporting"
                    reason = "fallback_supporting_effect_only"
            claim_matches.append(
                {
                    "claim": claim,
                    "matched_effect_type": chosen.get("effect_type") if isinstance(chosen, dict) else None,
                    "matched_effect_scope": chosen.get("effect_scope") if isinstance(chosen, dict) else None,
                    "condition_status": condition_status,
                    "matched_facts": matched_facts,
                    "missing_conditions": missing_conditions,
                    "proposed_coverage_type": proposed,
                    "evidence_quote": chosen.get("evidence_quote") if isinstance(chosen, dict) else None,
                    "reason": reason,
                    "confidence": float(chosen.get("confidence") or 0.0) if isinstance(chosen, dict) else 0.0,
                }
            )
        return {
            "article_number": str(article.get("article_number") or article_semantics.get("article_number") or ""),
            "article_title": str(article.get("article_title") or article.get("title") or article_semantics.get("article_title") or ""),
            "claim_matches": claim_matches,
            "overall_notes": ["fallback_claim_matcher_used"],
        }
