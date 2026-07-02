from typing import Any

from app.schemas.legal_rag import CoverageGateEntrySchema


class CoverageGate:
    CLAIM_EFFECTS = {
        "refund_principal": {"return_principal", "return_received", "termination_consequence", "termination_consequences"},
        "restitution": {"return_received", "termination_consequence", "termination_consequences"},
        "damages": {"damages_recovery"},
        "interest": {"interest", "interest_recovery"},
        "penalty": {"penalty", "penalty_or_security"},
        "performance": {"obligation_performance", "obligation_basis", "performance_terms", "delay_liability"},
        "termination/refusal": {"termination_or_refusal", "termination_consequence", "termination_consequences"},
    }
    SUPPORTING_EFFECTS = {
        "refund_principal": {"obligation_performance", "obligation_basis", "performance_terms", "termination_or_refusal"},
        "restitution": {"termination_or_refusal", "obligation_performance", "obligation_basis"},
        "damages": {"damages_definition", "liability_basis", "obligation_basis", "performance_terms", "delay_liability", "limitation_or_exception"},
        "interest": {"limitation_or_exception", "obligation_performance", "obligation_basis", "creditor_delay"},
        "penalty": {"liability_basis", "security_or_deposit", "penalty_or_security"},
        "performance": {"obligation_performance", "obligation_basis", "termination_or_refusal"},
        "termination/refusal": {"termination_consequence", "termination_consequences", "obligation_performance", "obligation_basis", "performance_terms"},
    }
    DIRECT_COVERAGE_TYPES = {"direct", "valid_conditional"}

    def evaluate(
        self,
        claim: str,
        article: dict[str, Any],
        match: dict[str, Any] | None,
        facts: dict[str, Any],
        user_text: str,
    ) -> dict[str, Any]:
        effect_type = str((match or {}).get("matched_effect_type") or "other")
        effect_scope = str((match or {}).get("matched_effect_scope") or "supporting")
        evidence_quote = str((match or {}).get("evidence_quote") or "").strip()
        trigger_conditions = self._trigger_conditions(article, effect_type)
        missing_conditions = self._missing_conditions(match, trigger_conditions, facts, user_text)
        effect_description = self._effect_description(article, effect_type)

        desired_effects = self.CLAIM_EFFECTS.get(claim, set())
        supporting_effects = self.SUPPORTING_EFFECTS.get(claim, set())
        proposed = str((match or {}).get("proposed_coverage_type") or "no_coverage")

        coverage_type = "no_coverage"
        reason = str((match or {}).get("reason") or "").strip()
        direct_scopes = {"general_direct", "special_conditional"}
        if effect_type in desired_effects and evidence_quote and effect_scope in direct_scopes:
            if effect_scope == "special_conditional" and missing_conditions:
                coverage_type = "conditional_missing_facts"
                if not reason:
                    reason = "special_conditional_effect_has_unmet_trigger_conditions"
            elif missing_conditions:
                coverage_type = "conditional_missing_facts"
                if not reason:
                    reason = "matched_effect_has_missing_conditions"
            elif proposed == "valid_conditional" or effect_scope == "special_conditional":
                coverage_type = "valid_conditional"
                if not reason:
                    reason = "matched_effect_strictly_covers_claim_with_verified_conditions"
            else:
                coverage_type = "direct"
                if not reason:
                    reason = "matched_effect_strictly_covers_claim"
        elif effect_type in supporting_effects and evidence_quote:
            coverage_type = "supporting"
            if not reason:
                reason = "matched_effect_is_supporting_context_only"
        elif proposed == "supporting" and evidence_quote:
            coverage_type = "supporting"
            if not reason:
                reason = "claim_matcher_marked_article_as_supporting"
        elif proposed == "conditional_missing_facts" and effect_type in desired_effects:
            coverage_type = "conditional_missing_facts"
            if not reason:
                reason = "claim_matcher_found_potential_match_with_missing_conditions"
        elif not reason:
            reason = "no_strict_claim_effect_match"

        guard_downgrade_reason = self._guard_downgrade_reason(claim, article, coverage_type, facts, user_text)
        if guard_downgrade_reason:
            coverage_type = "supporting" if coverage_type == "direct" else "no_coverage"
            reason = guard_downgrade_reason

        counts_as_covered = coverage_type in self.DIRECT_COVERAGE_TYPES
        user_visible = counts_as_covered and bool(evidence_quote) and not missing_conditions
        entry = CoverageGateEntrySchema(
            claim=claim,
            article_id=str(article.get("id") or ""),
            article_number=str(article.get("article_number") or ""),
            article_title=str(article.get("article_title") or article.get("title") or ""),
            effect_type=effect_type,
            effect_scope=effect_scope,
            coverage_type=coverage_type,
            counts_as_covered=counts_as_covered,
            user_visible=user_visible,
            trigger_conditions=trigger_conditions,
            trigger_conditions_satisfied=not missing_conditions,
            missing_conditions=missing_conditions,
            evidence_quote=evidence_quote,
            effect_description=effect_description,
            reason=reason,
        )
        payload = entry.model_dump()
        if guard_downgrade_reason:
            payload["guard_downgraded"] = True
            payload["guard_reason"] = guard_downgrade_reason
        return payload

    @staticmethod
    def _guard_downgrade_reason(
        claim: str,
        article: dict[str, Any],
        coverage_type: str,
        facts: dict[str, Any],
        user_text: str,
    ) -> str | None:
        article_number = str(article.get("article_number") or "").strip()
        if article_number != "167" or claim != "refund_principal" or coverage_type not in {"direct", "valid_conditional"}:
            return None
        text = CoverageGate._facts_text(facts, user_text)
        invalidity_tokens = ("недействительн", "ничтожн", "оспарив", "оспорим", "недействительность", "сделк")
        if any(token in text for token in invalidity_tokens):
            return None
        return "article_167_requires_invalid_transaction_context"

    def _missing_conditions(
        self,
        match: dict[str, Any] | None,
        trigger_conditions: list[str],
        facts: dict[str, Any],
        user_text: str,
    ) -> list[str]:
        if match and isinstance(match.get("missing_conditions"), list):
            return [str(item) for item in match.get("missing_conditions") if str(item).strip()]
        return [condition for condition in trigger_conditions if not self.condition_met(condition, facts, user_text)]

    @staticmethod
    def _effect_description(article: dict[str, Any], effect_type: str) -> str:
        for effect in CoverageGate._effects(article):
            if str(effect.get("effect_type") or "") == effect_type:
                return str(effect.get("effect_description") or "").strip()
        return ""

    @staticmethod
    def _trigger_conditions(article: dict[str, Any], effect_type: str) -> list[str]:
        for effect in CoverageGate._effects(article):
            if str(effect.get("effect_type") or "") == effect_type:
                values = effect.get("trigger_conditions") if isinstance(effect.get("trigger_conditions"), list) else []
                return [str(item) for item in values if str(item).strip()]
        return []

    @staticmethod
    def _effects(article: dict[str, Any]) -> list[dict[str, Any]]:
        semantics = article.get("semantic_analysis") if isinstance(article.get("semantic_analysis"), dict) else {}
        effects = semantics.get("legal_effects") if isinstance(semantics.get("legal_effects"), list) else []
        return [effect for effect in effects if isinstance(effect, dict)]

    @staticmethod
    def condition_met(condition: str, facts: dict[str, Any], user_text: str) -> bool:
        text = CoverageGate._facts_text(facts, user_text)
        problem = facts.get("problem") if isinstance(facts.get("problem"), dict) else {}
        demand = facts.get("demand") if isinstance(facts.get("demand"), dict) else {}
        transaction = facts.get("transaction") if isinstance(facts.get("transaction"), dict) else {}
        parties = facts.get("parties") if isinstance(facts.get("parties"), dict) else {}
        normalized_claims = facts.get("normalized_claims") if isinstance(facts.get("normalized_claims"), list) else []

        if condition == "invalid_transaction":
            return any(token in text for token in ("недействительн", "ничтожн", "оспорим"))
        if condition == "termination_or_refusal":
            return (
                "termination/refusal" in normalized_claims
                or str(demand.get("type") or "").lower() == "cancel_contract"
                or any(token in text for token in ("расторг", "отказ от договора", "отказаться от договора", "прекращение договора"))
            )
        if condition == "termination_consequences_context":
            return CoverageGate.condition_met("termination_or_refusal", facts, user_text) and (
                "refund_principal" in normalized_claims
                or str(demand.get("type") or "").lower() == "refund"
                or any(token in text for token in ("возврат", "вернуть", "не возвращает", "уплач"))
            )
        if condition == "deposit_or_security":
            return any(token in text for token in ("задат", "обеспечительный платеж", "обеспеч"))
        if condition == "unjust_enrichment":
            return "неосновательн" in text
        if condition == "breach_or_nonperformance":
            return str(problem.get("type") or "").lower() in {"nonperformance", "delay", "defective_service"} or any(
                token in text for token in ("неисполн", "не исполнил", "наруш", "ненадлежащ", "просроч", "не выполнил")
            )
        if condition == "money_retention_or_delay":
            has_refund_context = "refund_principal" in normalized_claims or str(demand.get("type") or "").lower() == "refund"
            has_money = bool(transaction.get("price_amount") or transaction.get("price") or transaction.get("payment_confirmed") or demand.get("amount"))
            has_refusal = bool(parties.get("opponent_response") or demand.get("requested_at")) or any(
                token in text for token in ("удерж", "не возвращает", "не вернул", "отказ")
            )
            return has_refund_context and has_money and has_refusal
        if condition == "missed_deadline_or_delay":
            return bool(problem.get("deadline") or problem.get("violation_date")) or any(
                token in text for token in ("просроч", "срок", "в срок", "неисполн", "не выполнил")
            )
        if condition == "performance_deadline":
            return bool(problem.get("deadline") or problem.get("violation_date")) or "срок" in text
        if condition == "guarantee_context":
            return any(token in text for token in ("бенефициар", "гарант", "принципал", "гаранти"))
        if condition == "regulatory_license_missing":
            return any(token in text for token in ("лиценз", "саморегулируем", "сро")) and any(
                token in text for token in ("нет", "отсутств", "не было", "без")
            )
        if condition == "creditor_delay_context":
            return any(token in text for token in ("кредитор отказался принять", "отказался принять исполнение", "просрочка кредитора"))
        if condition == "impossibility_context":
            return any(token in text for token in ("невозможность исполнения", "невозможно исполнить", "обстоятельство, за которое ни одна из сторон не отвечает"))
        if condition == "special_indemnity_agreement":
            return any(
                token in text
                for token in (
                    "соглашение о возмещении потерь",
                    "соглашением предусмотрено возмещение потерь",
                    "условие о возмещении потерь",
                    "договором предусмотрено возмещение потерь",
                )
            )
        if condition == "business_context":
            return any(token in text for token in ("предприниматель", "бизнес", "коммерческ"))
        return False

    @staticmethod
    def _facts_text(facts: dict[str, Any], user_text: str) -> str:
        parts = [str(user_text or ""), str(facts.get("summary") or "")]
        for key in ("transaction", "problem", "demand", "parties", "documents", "known_facts"):
            value = facts.get(key)
            if isinstance(value, dict):
                parts.extend(str(item) for item in value.values() if item)
            elif isinstance(value, list):
                parts.extend(str(item) for item in value if item)
        return " ".join(parts).lower()
