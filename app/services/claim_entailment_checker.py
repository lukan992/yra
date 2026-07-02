from typing import Any

from app.core.logging import log_json
from app.services.claim_matcher import ClaimMatcherLLM
from app.services.coverage_gate import CoverageGate


class ClaimEntailmentChecker:
    CLAIM_EFFECTS = CoverageGate.CLAIM_EFFECTS
    SUPPORTING_EFFECTS = CoverageGate.SUPPORTING_EFFECTS

    def __init__(
        self,
        claim_matcher: ClaimMatcherLLM | None = None,
        coverage_gate: CoverageGate | None = None,
    ) -> None:
        self.claim_matcher = claim_matcher or ClaimMatcherLLM()
        self.coverage_gate = coverage_gate or CoverageGate()

    def build_coverage(
        self,
        normalized_claims: list[str],
        articles: list[dict[str, Any]],
        facts: dict[str, Any],
        user_text: str,
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        coverage_entries: list[dict[str, Any]] = []
        claim_payloads: dict[str, dict[str, Any]] = {
            claim: {"covered": False, "covered_by": None, "coverage_type": "missing", "blocked_by_missing_facts": [], "supporting": []}
            for claim in normalized_claims
        }

        for article in articles:
            article_coverages: list[dict[str, Any]] = []
            semantics = article.get("semantic_analysis") if isinstance(article.get("semantic_analysis"), dict) else {}
            matcher_output = article.get("claim_matcher_output") if isinstance(article.get("claim_matcher_output"), dict) else None
            if not matcher_output and self.claim_matcher:
                matcher_output = self.claim_matcher.match(
                    facts,
                    normalized_claims,
                    semantics,
                    article,
                    request_id=request_id,
                    run_id=run_id,
                )
                article["claim_matcher_output"] = matcher_output

            match_index = {
                str(item.get("claim") or ""): item
                for item in (matcher_output.get("claim_matches") if isinstance(matcher_output, dict) and isinstance(matcher_output.get("claim_matches"), list) else [])
                if isinstance(item, dict) and str(item.get("claim") or "")
            }

            for claim in normalized_claims:
                entry = self.coverage_gate.evaluate(claim, article, match_index.get(claim), facts, user_text)
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
        result = {
            "claims": claim_payloads,
            "coverage_map": coverage_entries,
            "valid_covered_claims": valid_covered_claims,
            "missing_claims": missing_claims,
            "missing_coverage": {"claims": missing_claims, "roles": []},
            "blocked_by_missing_facts": blocked,
        }
        log_json(
            "legal_rag.coverage_gate.result",
            request_id=request_id,
            run_id=run_id,
            step="law_reranking",
            claims=result["claims"],
            coverage=result["coverage_map"],
            missing_claims=result["missing_claims"],
        )
        return result

    def _apply_claim_entry(self, payload: dict[str, Any], entry: dict[str, Any]) -> None:
        coverage_type = str(entry.get("coverage_type") or "")
        if entry.get("counts_as_covered"):
            if not payload.get("covered") or coverage_type == "direct":
                payload["covered"] = True
                payload["covered_by"] = self._summarize_entry(entry)
                payload["coverage_type"] = coverage_type
            return
        if coverage_type == "conditional_missing_facts" and entry.get("missing_conditions"):
            existing = payload.setdefault("blocked_by_missing_facts", [])
            for missing in entry.get("missing_conditions") or []:
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
            "effect_scope": entry.get("effect_scope"),
            "evidence_quote": entry.get("evidence_quote"),
        }

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

    @staticmethod
    def _condition_met(condition: str, facts: dict[str, Any], user_text: str) -> bool:
        return CoverageGate.condition_met(condition, facts, user_text)
