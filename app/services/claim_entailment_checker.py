from typing import Any


class ClaimEntailmentChecker:
    CLAIM_EFFECTS = {
        "refund_principal": {"return_principal", "return_received", "termination_consequences"},
        "restitution": {"return_received", "termination_consequences"},
        "damages": {"damages_recovery"},
        "interest": {"interest_recovery"},
        "performance": {"performance_terms", "delay_liability"},
        "termination/refusal": {"termination_or_refusal", "termination_consequences"},
    }
    SUPPORTING_EFFECTS = {
        "refund_principal": {"obligation_basis", "performance_terms", "termination_or_refusal", "penalty_or_security"},
        "restitution": {"termination_or_refusal", "obligation_basis"},
        "damages": {"damages_definition", "obligation_basis", "delay_liability", "limitation_or_exception"},
        "interest": {"creditor_delay", "limitation_or_exception", "obligation_basis"},
        "performance": {"obligation_basis", "termination_or_refusal"},
        "termination/refusal": {"termination_consequences", "obligation_basis", "performance_terms"},
    }

    def build_coverage(
        self,
        normalized_claims: list[str],
        articles: list[dict[str, Any]],
        facts: dict[str, Any],
        user_text: str,
    ) -> dict[str, Any]:
        coverage_entries: list[dict[str, Any]] = []
        claim_payloads: dict[str, dict[str, Any]] = {
            claim: {"covered": False, "covered_by": None, "coverage_type": "missing", "blocked_by_missing_facts": [], "supporting": []}
            for claim in normalized_claims
        }

        for article in articles:
            article_coverages: list[dict[str, Any]] = []
            for claim in normalized_claims:
                for effect in self._effects(article):
                    entry = self.check(claim, article, effect, facts, user_text)
                    coverage_entries.append(entry)
                    article_coverages.append(entry)
                    self._apply_claim_entry(claim_payloads[claim], entry)
            article["coverage"] = article_coverages
            article["covers_claims"] = sorted(
                {
                    str(entry.get("claim"))
                    for entry in article_coverages
                    if entry.get("counts_as_covered") and isinstance(entry.get("claim"), str)
                }
            )
            article["coverage_type"] = self._article_coverage_type(article_coverages)

        valid_covered_claims = [claim for claim, payload in claim_payloads.items() if payload.get("covered")]
        missing_claims = [claim for claim in normalized_claims if claim not in valid_covered_claims]
        blocked = [
            {"claim": claim, "missing_facts": payload.get("blocked_by_missing_facts", [])}
            for claim, payload in claim_payloads.items()
            if payload.get("blocked_by_missing_facts")
        ]
        return {
            "claims": claim_payloads,
            "coverage_map": coverage_entries,
            "valid_covered_claims": valid_covered_claims,
            "missing_claims": missing_claims,
            "missing_coverage": {"claims": missing_claims, "roles": []},
            "blocked_by_missing_facts": blocked,
        }

    def check(
        self,
        claim: str,
        article: dict[str, Any],
        effect: dict[str, Any],
        facts: dict[str, Any],
        user_text: str,
    ) -> dict[str, Any]:
        desired_effects = self.CLAIM_EFFECTS.get(claim, set())
        effect_type = str(effect.get("effect_type") or "other")
        evidence_quote = str(effect.get("evidence_quote") or "").strip()
        trigger_conditions = [str(item) for item in effect.get("trigger_conditions") if isinstance(item, str)] if isinstance(effect.get("trigger_conditions"), list) else []
        missing_facts = [condition for condition in trigger_conditions if not self._condition_met(condition, facts, user_text)]

        if effect_type in desired_effects and evidence_quote:
            if not missing_facts:
                coverage_type = "valid_conditional" if trigger_conditions else "direct"
                counts_as_covered = True
            else:
                coverage_type = "conditional_missing_facts"
                counts_as_covered = False
        elif effect_type in self.SUPPORTING_EFFECTS.get(claim, set()) and evidence_quote:
            coverage_type = "supporting"
            counts_as_covered = False
        else:
            coverage_type = "no_coverage"
            counts_as_covered = False

        return {
            "claim": claim,
            "article_id": str(article.get("id") or ""),
            "article_number": article.get("article_number"),
            "article_title": article.get("article_title") or article.get("title"),
            "effect_type": effect_type,
            "coverage_type": coverage_type,
            "counts_as_covered": counts_as_covered,
            "trigger_conditions": trigger_conditions,
            "missing_facts": missing_facts,
            "evidence_quote": evidence_quote,
            "effect_description": effect.get("effect_description"),
        }

    def _apply_claim_entry(self, payload: dict[str, Any], entry: dict[str, Any]) -> None:
        coverage_type = str(entry.get("coverage_type") or "")
        if entry.get("counts_as_covered"):
            if not payload.get("covered") or coverage_type == "direct":
                payload["covered"] = True
                payload["covered_by"] = self._summarize_entry(entry)
                payload["coverage_type"] = coverage_type
            return
        if coverage_type == "conditional_missing_facts" and entry.get("missing_facts"):
            existing = payload.setdefault("blocked_by_missing_facts", [])
            for missing in entry.get("missing_facts") or []:
                if missing not in existing:
                    existing.append(missing)
            if payload.get("coverage_type") == "missing":
                payload["coverage_type"] = "blocked_by_missing_facts"
        elif coverage_type == "supporting":
            supporting = payload.setdefault("supporting", [])
            summary = self._summarize_entry(entry)
            if summary not in supporting:
                supporting.append(summary)

    @staticmethod
    def _summarize_entry(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": entry.get("article_id"),
            "article_number": entry.get("article_number"),
            "title": entry.get("article_title"),
            "coverage_type": entry.get("coverage_type"),
            "effect_type": entry.get("effect_type"),
            "evidence_quote": entry.get("evidence_quote"),
        }

    @staticmethod
    def _effects(article: dict[str, Any]) -> list[dict[str, Any]]:
        semantics = article.get("semantic_analysis") if isinstance(article.get("semantic_analysis"), dict) else {}
        effects = semantics.get("legal_effects") if isinstance(semantics.get("legal_effects"), list) else []
        return [effect for effect in effects if isinstance(effect, dict)]

    @staticmethod
    def _article_coverage_type(entries: list[dict[str, Any]]) -> str:
        types = [str(entry.get("coverage_type") or "") for entry in entries]
        if "direct" in types:
            return "direct"
        if "valid_conditional" in types:
            return "valid_conditional"
        if "conditional_missing_facts" in types:
            return "conditional_missing_facts"
        if "supporting" in types:
            return "supporting"
        return "no_coverage"

    def _condition_met(self, condition: str, facts: dict[str, Any], user_text: str) -> bool:
        text = self._facts_text(facts, user_text)
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
            return self._condition_met("termination_or_refusal", facts, user_text) and (
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
