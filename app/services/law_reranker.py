import json
from typing import Any

from app.core.config import get_settings
from app.core.logging import log_json
from app.schemas.pipeline import LLMError
from app.services.article_semantic_analyzer import ArticleSemanticAnalyzer
from app.services.article_role_registry import ArticleRoleRegistry, ArticleRoleRule
from app.services.claim_matcher import ClaimMatcherLLM
from app.services.claim_entailment_checker import ClaimEntailmentChecker
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader
from app.services.reranker_client import RerankerClient


class LawReranker:
    CLAIM_ROLE_PRIORITY = {
        "refund_principal": ["refund_or_restitution", "termination_or_refusal", "performance_terms"],
        "interest": ["monetary_obligation_interest"],
        "damages": ["damages_recovery", "damages_definition", "liability_basis"],
        "performance": ["performance_terms", "obligation_basis", "breach_or_delay"],
        "termination/refusal": ["termination_or_refusal", "refund_or_restitution", "obligation_basis"],
        "penalty": ["penalty_or_security", "liability_basis"],
        "restitution": ["refund_or_restitution", "termination_or_refusal"],
        "other": ["obligation_basis", "breach_or_delay", "procedure"],
    }
    ROLE_PRIORITY = [
        "obligation_basis",
        "performance_terms",
        "breach_or_delay",
        "liability_basis",
        "damages_definition",
        "damages_recovery",
        "refund_or_restitution",
        "termination_or_refusal",
        "monetary_obligation_interest",
        "procedure",
        "penalty_or_security",
        "exception_or_limitation",
    ]
    VALID_ROLES = set(ROLE_PRIORITY + ["weak_or_unrelated"])
    POSITIVE_APPLICABILITY = {"direct", "related", "weak"}
    MAX_SELECTED_ARTICLES = 12
    MAX_SELECTED_HARD_LIMIT = 15
    MAX_TRACE_DROPPED = 12
    HIGH_SCORE_THRESHOLD = 0.65
    INTERNAL_WHY_RELEVANT_MARKERS = (
        "Fallback reranker used because the LLM reranker was unavailable.",
        "Статья сохранена для проверки claim/fact coverage правовой конструкции.",
    )

    def __init__(
        self,
        llm_client: LiteLLMClient,
        prompt_loader: PromptLoader,
        reranker_client: RerankerClient | None = None,
    ) -> None:
        self.litellm_client = llm_client
        self.prompt_loader = prompt_loader
        self.settings = get_settings()
        self.role_registry = ArticleRoleRegistry()
        self.semantic_analyzer = ArticleSemanticAnalyzer(llm_client, prompt_loader)
        self.claim_matcher = ClaimMatcherLLM(llm_client, prompt_loader)
        self.entailment_checker = ClaimEntailmentChecker(self.claim_matcher)
        self.reranker_client = reranker_client or RerankerClient()
        self.last_trace: dict[str, Any] = {}

    def rerank(
        self,
        user_text: str,
        facts: dict[str, Any],
        legal_area: dict[str, Any],
        candidate_articles: list[dict[str, Any]],
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not candidate_articles:
            return []

        facts_for_reranker = self._normalize_facts(facts)
        normalized_claims = self._normalized_claims(facts_for_reranker, user_text)
        rerank_input_max = max(1, int(self.settings.legal_rag_rerank_input_max))
        llm_top_k = max(1, int(self.settings.legal_rag_llm_article_top_k or self.settings.legal_rag_rerank_top_k))
        rerank_candidates = [dict(article) for article in candidate_articles[:rerank_input_max]]
        for article in rerank_candidates:
            article["hybrid_score"] = float(
                article.get("combined_score") or article.get("keyword_score") or article.get("vector_score") or 0.0
            )

        rerank_query = self._build_rerank_query(user_text, facts_for_reranker, legal_area, normalized_claims)
        self._log_reranker_input(
            candidate_articles,
            rerank_candidates,
            rerank_input_max=rerank_input_max,
            request_id=request_id,
            run_id=run_id,
        )
        rerank_fallback = False
        try:
            ranked_candidates = self._apply_reranker(
                rerank_query,
                rerank_candidates,
                request_id=request_id,
                run_id=run_id,
            )
        except LLMError:
            rerank_fallback = True
            ranked_candidates = self._fallback_ranked_items(rerank_candidates, facts_for_reranker)

        reranked_for_llm = self._select_llm_candidates(
            ranked_candidates,
            facts_for_reranker,
            legal_area,
            normalized_claims,
            llm_top_k,
            rerank_fallback=rerank_fallback,
            request_id=request_id,
            run_id=run_id,
        )
        self._log_reranker_output(
            ranked_candidates,
            reranked_for_llm,
            rerank_fallback=rerank_fallback,
            request_id=request_id,
            run_id=run_id,
        )
        semantic_candidates = self._attach_semantics(reranked_for_llm, request_id=request_id, run_id=run_id)

        role_corrections: list[dict[str, Any]] = []
        enriched = self._enrich_ranked_items(
            user_text,
            facts_for_reranker,
            normalized_claims,
            semantic_candidates,
            semantic_candidates,
            role_corrections,
        )
        selected, coverage = self._select_by_entailment(
            user_text,
            facts_for_reranker,
            normalized_claims,
            enriched,
            request_id=request_id,
            run_id=run_id,
        )
        self.last_trace = self._build_trace(
            rerank_candidates,
            facts_for_reranker,
            normalized_claims,
            enriched,
            selected,
            coverage,
            role_corrections,
            ranked_candidates=ranked_candidates,
            llm_candidates=semantic_candidates,
            rerank_query=rerank_query,
            rerank_fallback=rerank_fallback,
        )
        return selected

    def _log_reranker_input(
        self,
        candidate_articles: list[dict[str, Any]],
        rerank_candidates: list[dict[str, Any]],
        *,
        rerank_input_max: int,
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        log_json(
            "legal_rag.reranker.input",
            request_id=request_id,
            run_id=run_id,
            step="law_reranking",
            rerank_input_max=rerank_input_max,
            total_candidates_found=len(candidate_articles),
            sent_to_reranker=len(rerank_candidates),
            candidates=[self._summarize_candidate(article) for article in rerank_candidates[:12]],
        )

    def _apply_reranker(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        results = self.reranker_client.rerank(
            query,
            candidates,
            top_n=len(candidates),
            request_id=request_id,
            run_id=run_id,
        )
        ranked_by_index = {int(item["index"]): float(item["relevance_score"]) for item in results}
        ranked_candidates: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            item = dict(candidate)
            rerank_score = ranked_by_index.get(index)
            if rerank_score is None:
                rerank_score = float(item.get("hybrid_score") or 0.0)
                item["rerank_reason"] = "missing_rerank_score_fallback_to_hybrid"
            else:
                item["rerank_reason"] = "qwen_reranker"
            item["relevance_score"] = rerank_score
            item["rerank_score"] = rerank_score
            ranked_candidates.append(item)
        return sorted(ranked_candidates, key=self._rerank_sort_key, reverse=True)

    def _select_llm_candidates(
        self,
        ranked_candidates: list[dict[str, Any]],
        facts: dict[str, Any],
        legal_area: dict[str, Any],
        normalized_claims: list[str],
        llm_top_k: int,
        *,
        rerank_fallback: bool,
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        scenario = self._consumer_service_scenario(facts, legal_area, normalized_claims)
        limit = (
            max(1, int(self.settings.legal_rag_llm_article_top_k_on_rerank_fail))
            if rerank_fallback
            else llm_top_k
        )
        decorated: list[dict[str, Any]] = []
        for candidate in ranked_candidates:
            item = dict(candidate)
            filter_result = self._pre_llm_filter(item, facts, legal_area, normalized_claims, scenario)
            candidate.update(filter_result)
            item.update(filter_result)
            boost, reasons, boost_strength = self._llm_selection_boosts(item, facts, legal_area, normalized_claims, scenario)
            item["selection_boost"] = boost
            item["selection_boost_reasons"] = reasons
            item["boost_strength"] = boost_strength
            if reasons:
                item["boost_reason"] = ", ".join(reasons)
            log_json(
                f"legal_rag.pre_llm_filter.{ 'kept' if item.get('pre_llm_keep') else 'excluded' }",
                request_id=request_id,
                run_id=run_id,
                article_number=item.get("article_number"),
                title=item.get("article_title") or item.get("title"),
                act_name=item.get("act_name"),
                reason=item.get("pre_llm_reason"),
                case_type=facts.get("preliminary_case_type"),
                detected_claims=normalized_claims,
                matched_negative_domain=item.get("matched_negative_domain"),
                matched_negative_keywords=item.get("matched_negative_keywords"),
                negative_overridden_by_positive=item.get("negative_overridden_by_positive"),
                negative_domain_decision_reason=item.get("negative_domain_decision_reason"),
                missing_positive_signals=item.get("missing_positive_signals"),
                matched_positive_signals=item.get("matched_positive_signals"),
                legal_role=item.get("legal_role"),
                hybrid_score=item.get("hybrid_score"),
                rerank_score=item.get("rerank_score"),
            )
            decorated.append(item)
        kept = [item for item in decorated if item.get("pre_llm_keep")]
        if len(kept) < min(2, limit):
            kept = self._rescue_targeted_candidates(decorated, kept, limit)
        selected = sorted(kept, key=self._llm_selection_sort_key, reverse=True)[:limit]
        for item in selected:
            item["selection_reason"] = item.get("boost_reason") or "highest_rerank_score"
        if rerank_fallback:
            for item in selected:
                item["selection_reason"] = f"fallback_mode:{item.get('selection_reason')}"
        return selected

    def _log_reranker_output(
        self,
        ranked_candidates: list[dict[str, Any]],
        llm_candidates: list[dict[str, Any]],
        *,
        rerank_fallback: bool,
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        log_json(
            "legal_rag.reranker.output_top",
            request_id=request_id,
            run_id=run_id,
            step="law_reranking",
            rerank_fallback=rerank_fallback,
            top_candidates=[self._summarize_candidate(article) for article in ranked_candidates[:12]],
        )
        log_json(
            "legal_rag.llm_articles.selected",
            request_id=request_id,
            run_id=run_id,
            step="law_reranking",
            selected_for_llm=len(llm_candidates),
            articles=[self._summarize_candidate(article) for article in llm_candidates],
        )

    def _build_rerank_query(
        self,
        user_text: str,
        facts: dict[str, Any],
        legal_area: dict[str, Any],
        normalized_claims: list[str],
    ) -> str:
        query_parts = [
            str(facts.get("summary") or ""),
            str(legal_area.get("primary_area") or ""),
            " ".join(normalized_claims),
            str((facts.get("transaction") or {}).get("type") if isinstance(facts.get("transaction"), dict) else ""),
            str((facts.get("problem") or {}).get("type") if isinstance(facts.get("problem"), dict) else ""),
            str((facts.get("demand") or {}).get("type") if isinstance(facts.get("demand"), dict) else ""),
            str(user_text or ""),
        ]
        return " ".join(part for part in query_parts if part).strip()

    @staticmethod
    def _rerank_sort_key(item: dict[str, Any]) -> tuple[float, float]:
        return (
            float(item.get("rerank_score") or item.get("relevance_score") or 0.0),
            float(item.get("hybrid_score") or item.get("combined_score") or 0.0),
        )

    def _llm_selection_sort_key(self, item: dict[str, Any]) -> tuple[float, float, float]:
        return (
            float(item.get("selection_boost") or 0.0),
            float(item.get("rerank_score") or item.get("relevance_score") or 0.0),
            float(item.get("hybrid_score") or item.get("combined_score") or 0.0),
        )

    @staticmethod
    def _consumer_service_scenario(
        facts: dict[str, Any],
        legal_area: dict[str, Any],
        normalized_claims: list[str],
    ) -> bool:
        transaction = facts.get("transaction") if isinstance(facts.get("transaction"), dict) else {}
        legal_area_secondary = {
            str(item).lower() for item in (legal_area.get("secondary_areas") if isinstance(legal_area.get("secondary_areas"), list) else [])
        }
        transaction_type = str(transaction.get("type") or transaction.get("item_or_service") or "").lower()
        return (
            str(facts.get("preliminary_case_type") or "").lower() == "contract_nonperformance"
            or str(legal_area.get("case_type") or "").lower() == "contract_nonperformance"
            or (
                "refund_principal" in normalized_claims
                and "consumer" in legal_area_secondary
                and any(token in transaction_type for token in ("услуг", "оказан"))
            )
        )

    def _llm_selection_boosts(
        self,
        article: dict[str, Any],
        facts: dict[str, Any],
        legal_area: dict[str, Any],
        normalized_claims: list[str],
        consumer_service_scenario: bool,
    ) -> tuple[float, list[str], float]:
        score = float(article.get("rerank_score") or article.get("relevance_score") or 0.0)
        boost = score
        reasons: list[str] = []
        boost_strength = 0.0
        act_name = str(article.get("act_name") or "").lower()
        text = " ".join(
            [
                str(article.get("article_title") or ""),
                str(article.get("title") or ""),
                str(article.get("snippet") or ""),
                str(article.get("article_text") or ""),
            ]
        ).lower()
        if article.get("matched_negative_domain"):
            return boost, reasons, boost_strength
        if consumer_service_scenario and "refund_principal" in normalized_claims:
            service_terms = ("услуг", "оказан", "заказчик", "исполнитель", "срок оказания")
            consumer_terms = ("потребител", "защите прав потребителей", "зозпп")
            refund_terms = ("возврат", "вернуть", "уплачен", "отказ от договора")
            if any(token in text for token in service_terms):
                boost += 0.25
                reasons.append("consumer_service_guard")
                boost_strength += 0.25
            if any(token in text for token in consumer_terms) or "защите прав потребителей" in act_name:
                boost += 0.3
                reasons.append("consumer_act_boost")
                boost_strength += 0.3
            if any(token in text for token in refund_terms):
                boost += 0.12
                reasons.append("refund_norm_boost")
                boost_strength += 0.12
        if "damages" in normalized_claims and any(token in text for token in ("убыт", "возмещ")):
            boost += 0.08
            reasons.append("damages_norm_boost")
            boost_strength += 0.08
        if str(article.get("article_number") or "") == "167" and not self._has_invalid_transaction_context(facts, legal_area):
            boost -= 0.5
            reasons.append("article_167_guard_penalty")
        return boost, reasons, boost_strength

    def _pre_llm_filter(
        self,
        article: dict[str, Any],
        facts: dict[str, Any],
        legal_area: dict[str, Any],
        normalized_claims: list[str],
        consumer_service_scenario: bool,
    ) -> dict[str, Any]:
        text = " ".join(
            [
                str(article.get("act_name") or ""),
                str(article.get("article_title") or article.get("title") or ""),
                str(article.get("snippet") or article.get("article_text") or ""),
            ]
        ).lower()
        expected_acts = {str(item).lower() for item in (legal_area.get("expected_acts") or []) if isinstance(item, str)}
        act_name = str(article.get("act_name") or "").lower()
        expected_domain_hit = not expected_acts or any(item in act_name for item in expected_acts)
        score = float(article.get("rerank_score") or article.get("relevance_score") or article.get("hybrid_score") or 0.0)
        positive_signals = [token for token in self._positive_signals() if token in text]
        strong_positive_signals = [token for token in self._strong_positive_signals() if token in text]
        matched_negative_domain, matched_negative_keywords = self._match_negative_domain(text, normalized_claims)
        strong_support = consumer_service_scenario and any(token in text for token in ("услуг", "потребител", "возврат", "договор"))
        negative_overridden_by_positive = bool(matched_negative_domain and strong_positive_signals)

        keep = False
        reason = "off_topic_or_missing_positive_signals"
        negative_domain_decision_reason = "insufficient_positive_signals"

        if matched_negative_domain and not negative_overridden_by_positive:
            reason = "off_topic_negative_domain"
            negative_domain_decision_reason = "negative_domain_strong_no_positive"
        elif expected_domain_hit and (strong_positive_signals or (positive_signals and score >= self.HIGH_SCORE_THRESHOLD)):
            keep = True
            reason = "direct_or_supporting_domain_match"
            negative_domain_decision_reason = (
                "positive_contract_signals_override_negative"
                if matched_negative_domain
                else "expected_domain_direct_match"
            )
        elif strong_support and strong_positive_signals:
            keep = True
            reason = "positive_contract_signals"
            negative_domain_decision_reason = (
                "positive_contract_signals_override_negative"
                if matched_negative_domain
                else "consumer_service_positive_signals"
            )
        elif score >= self.HIGH_SCORE_THRESHOLD and strong_positive_signals:
            keep = True
            reason = "score_supported_by_positive_signals"
            negative_domain_decision_reason = (
                "positive_contract_signals_override_negative"
                if matched_negative_domain
                else "high_score_with_positive_signals"
            )
        elif matched_negative_domain is None and expected_domain_hit and score >= self.HIGH_SCORE_THRESHOLD:
            keep = True
            reason = "expected_domain_score_match"
            negative_domain_decision_reason = "expected_domain_direct_match"
        elif matched_negative_domain is None and expected_domain_hit and not positive_signals and not str(facts.get("summary") or "").strip():
            keep = True
            reason = "no_context_expected_domain_fallback"
            negative_domain_decision_reason = "expected_domain_direct_match"

        return {
            "pre_llm_keep": keep,
            "pre_llm_reason": reason,
            "matched_positive_signals": positive_signals,
            "missing_positive_signals": [] if positive_signals else ["contract_or_service_or_refund_or_damages"],
            "matched_negative_domain": matched_negative_domain,
            "matched_negative_keywords": matched_negative_keywords,
            "negative_overridden_by_positive": negative_overridden_by_positive,
            "negative_domain_decision_reason": negative_domain_decision_reason,
        }

    @staticmethod
    def _positive_signals() -> tuple[str, ...]:
        return (
            "договор",
            "обязатель",
            "неисполн",
            "ненадлежащ",
            "просроч",
            "услуг",
            "работ",
            "заказчик",
            "исполнитель",
            "потребител",
            "возврат",
            "расторжен",
            "убыт",
            "ответственност",
            "защите прав потребителей",
            "кредитор",
            "должник",
            "оплат",
        )

    @staticmethod
    def _strong_positive_signals() -> tuple[str, ...]:
        return (
            "договор",
            "неисполн",
            "ненадлежащ",
            "просроч",
            "услуг",
            "работ",
            "заказчик",
            "исполнитель",
            "возврат",
            "убыт",
            "ответственност",
            "оплат",
        )

    @staticmethod
    def _match_negative_domain(text: str, normalized_claims: list[str]) -> tuple[str | None, list[str]]:
        negative_domains = {
            "security": (
                "залог",
                "ипотек",
                "поручитель",
                "поручительств",
                "банковская гарантия",
                "независимая гарантия",
                "залогодержат",
                "залогодатель",
            ),
            "property": ("вещн", "собственност", "земельн"),
            "corporate": ("акционер", "участник общества", "корпоратив"),
            "family": ("брак", "алименты", "супруг"),
            "inheritance": ("наслед", "завещан"),
            "labor": ("работодатель", "увольнен", "трудов"),
            "administrative": ("административ", "госуслуг", "разрешени"),
            "precontract": ("переговор", "преддоговор"),
            "tort": ("деликт",) if "damages" in normalized_claims else ("вред", "деликт"),
        }
        for domain, tokens in negative_domains.items():
            matched = [token for token in tokens if token and token in text]
            if matched:
                return domain, matched
        return None, []

    @staticmethod
    def _rescue_targeted_candidates(
        decorated: list[dict[str, Any]],
        kept: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        rescued = list(kept)
        for item in decorated:
            if item in rescued:
                continue
            positives = item.get("matched_positive_signals") if isinstance(item.get("matched_positive_signals"), list) else []
            if item.get("matched_negative_domain") is None and len(positives) >= 2:
                rescued.append(item)
            if len(rescued) >= limit:
                break
        return rescued

    @staticmethod
    def _has_invalid_transaction_context(facts: dict[str, Any], legal_area: dict[str, Any]) -> bool:
        text = " ".join(
            [
                str(facts.get("summary") or ""),
                str(legal_area.get("reason") or ""),
                str((facts.get("problem") or {}).get("description") if isinstance(facts.get("problem"), dict) else ""),
            ]
        ).lower()
        return any(token in text for token in ("недействитель", "ничтожн", "оспарив", "сделк"))

    def _attach_semantics(
        self,
        candidate_articles: list[dict[str, Any]],
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for article in candidate_articles:
            item = dict(article)
            semantic = self.semantic_analyzer.analyze(item, request_id=request_id, run_id=run_id)
            item["semantic_analysis"] = semantic
            item["semantic_summary"] = semantic.get("semantic_summary")
            item["legal_effects"] = semantic.get("legal_effects", [])
            result.append(item)
        return result

    def _semantic_prefilter(self, candidate_articles: list[dict[str, Any]], normalized_claims: list[str]) -> list[dict[str, Any]]:
        desired_effects: set[str] = set()
        for claim in normalized_claims:
            desired_effects.update(self.entailment_checker.CLAIM_EFFECTS.get(claim, set()))
        buckets: dict[str, list[dict[str, Any]]] = {}
        for article in candidate_articles:
            effects = article.get("legal_effects") if isinstance(article.get("legal_effects"), list) else []
            effect_types = {str(effect.get("effect_type") or "other") for effect in effects if isinstance(effect, dict)}
            bucket_key = next((effect for effect in effect_types if effect in desired_effects), next(iter(effect_types), "other"))
            buckets.setdefault(bucket_key, []).append(article)

        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for bucket in buckets.values():
            for article in sorted(bucket, key=self._sort_key, reverse=True)[:4]:
                article_id = str(article.get("id") or "")
                if article_id in seen:
                    continue
                seen.add(article_id)
                selected.append(article)
        for article in sorted(candidate_articles, key=self._sort_key, reverse=True):
            if len(selected) >= 18:
                break
            article_id = str(article.get("id") or "")
            if article_id in seen:
                continue
            seen.add(article_id)
            selected.append(article)
        return selected[:18]

    @staticmethod
    def _compact_llm_candidates(candidate_articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for article in candidate_articles:
            result.append(
                {
                    "id": str(article.get("id") or ""),
                    "act_name": article.get("act_name"),
                    "article_number": article.get("article_number"),
                    "article_title": article.get("article_title"),
                    "snippet": str(article.get("snippet") or article.get("article_text") or "")[:600],
                    "keyword_score": article.get("keyword_score"),
                    "vector_score": article.get("vector_score"),
                    "combined_score": article.get("combined_score"),
                    "semantic_summary": article.get("semantic_summary"),
                    "legal_effects": article.get("legal_effects"),
                }
            )
        return result

    def _merge_llm_ranked_items(self, candidate_articles: list[dict[str, Any]], raw: Any) -> list[dict[str, Any]]:
        items = raw.get("items") if isinstance(raw, dict) else raw
        allowed_ids = {str(item["id"]) for item in candidate_articles}
        candidates_by_id = {str(item["id"]): item for item in candidate_articles}
        result: list[dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            article_id = str(item.get("article_id") or "")
            if article_id not in allowed_ids:
                continue
            merged = dict(candidates_by_id[article_id])
            merged.update(
                {
                    "relevance_score": float(item.get("relevance_score") or 0.0),
                    "applicability": self._normalize_applicability(item.get("applicability")),
                    "why_relevant": item.get("why_relevant"),
                    "regulates": item.get("regulates"),
                    "missing_facts": item.get("missing_facts") if isinstance(item.get("missing_facts"), list) else [],
                    "legal_role": item.get("legal_role"),
                    "covers_claims": item.get("covers_claims") if isinstance(item.get("covers_claims"), list) else [],
                }
            )
            result.append(merged)
        return result

    def _fallback_ranked_items(self, candidate_articles: list[dict[str, Any]], facts: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for candidate in candidate_articles:
            score = float(candidate.get("hybrid_score") or candidate.get("combined_score") or candidate.get("keyword_score") or candidate.get("vector_score") or 0.0)
            applicability = self._fallback_applicability(score)
            merged = dict(candidate)
            merged.update(
                {
                    "relevance_score": score,
                    "rerank_score": score,
                    "applicability": applicability,
                    "why_relevant": "Fallback reranker used because the LLM reranker was unavailable.",
                    "rerank_reason": "hybrid_score_fallback",
                    "regulates": candidate.get("article_title") or candidate.get("chapter_title"),
                    "missing_facts": self._infer_missing_facts(candidate, facts),
                    "legal_role": None,
                    "fallback_legal_role": self._infer_role(candidate),
                    "covers_claims": [],
                }
            )
            result.append(merged)
        return result

    def _enrich_ranked_items(
        self,
        user_text: str,
        facts: dict[str, Any],
        normalized_claims: list[str],
        candidate_articles: list[dict[str, Any]],
        ranked_items: list[dict[str, Any]],
        role_corrections: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ranked_by_id = {str(item["id"]): dict(item) for item in ranked_items}
        enriched: list[dict[str, Any]] = []

        for candidate in candidate_articles:
            article_id = str(candidate["id"])
            item = ranked_by_id.get(article_id, dict(candidate))
            if article_id not in ranked_by_id:
                base_score = float(candidate.get("combined_score") or candidate.get("keyword_score") or candidate.get("vector_score") or 0.0)
                item.update(
                    {
                        "relevance_score": min(base_score * 0.9, 0.89),
                        "applicability": self._fallback_applicability(base_score),
                        "why_relevant": "Статья сохранена для проверки claim/fact coverage правовой конструкции.",
                        "regulates": candidate.get("article_title") or candidate.get("chapter_title"),
                        "covers_claims": [],
                    }
                )

            llm_role = self._normalize_role(item.get("legal_role"))
            fallback_role = self._normalize_role(item.get("fallback_legal_role"))
            inferred_role = llm_role or fallback_role or self._infer_role(item)
            rule = self.role_registry.lookup(item.get("act_name"), item.get("article_number"))
            effective_role = inferred_role

            if rule:
                if llm_role:
                    if llm_role not in rule.allowed_roles:
                        effective_role = rule.default_role
                        role_corrections.append(
                            {
                                "id": str(item.get("id") or ""),
                                "act_name": item.get("act_name"),
                                "article_number": item.get("article_number"),
                                "from_role": llm_role,
                                "to_role": effective_role,
                                "reason": "registry_disallowed_role",
                            }
                        )
                    else:
                        effective_role = llm_role
                else:
                    effective_role = rule.default_role

            item["llm_legal_role"] = llm_role
            item["legal_role"] = effective_role
            item["missing_facts"] = item.get("missing_facts") if isinstance(item.get("missing_facts"), list) else self._infer_missing_facts(item, facts)
            item["applicability"] = self._normalize_applicability(item.get("applicability")) or self._fallback_applicability(
                float(item.get("relevance_score") or item.get("combined_score") or item.get("keyword_score") or 0.0)
            )
            item["covers_claims"] = list(rule.can_cover_claims) if rule else self._claims_for_role(effective_role)
            item["cannot_cover_claims"] = list(rule.cannot_cover_claims) if rule else []
            item["registry_conditions"] = list(rule.conditions) if rule else []
            item["registry_conditions_met"] = self._conditions_met(rule, facts, user_text, normalized_claims)
            item["registry_failed_conditions"] = self._failed_conditions(rule, facts, user_text, normalized_claims)
            item["coverage_fact_match"] = self._fact_match_score(item, facts, user_text)
            item["coverage_penalty"] = self._missing_facts_penalty(item)
            item["why_relevant"] = self._user_visible_why_relevant(item, rule)
            enriched.append(item)
        return sorted(enriched, key=self._sort_key, reverse=True)

    def _select_balanced_articles(
        self,
        user_text: str,
        facts: dict[str, Any],
        normalized_claims: list[str],
        ranked_items: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        dropped_relevant: list[dict[str, Any]] = []
        role_buckets: dict[str, list[dict[str, Any]]] = {role: [] for role in self.ROLE_PRIORITY}
        eligible_items: list[dict[str, Any]] = []
        positive_items: list[dict[str, Any]] = []
        claim_coverage: dict[str, dict[str, Any]] = {}
        role_coverage: dict[str, dict[str, Any]] = {}

        for item in ranked_items:
            role = self._normalize_role(item.get("legal_role")) or "weak_or_unrelated"
            item["legal_role"] = role
            drop_reason = self._conditional_drop_reason(role, item, facts, user_text, normalized_claims)
            if drop_reason:
                item["coverage_drop_reason"] = drop_reason
                if self._is_relevant_drop(item):
                    dropped_relevant.append(self._dropped_candidate_summary(item, drop_reason))
                continue
            if item.get("applicability") in self.POSITIVE_APPLICABILITY:
                positive_items.append(item)
            if role in role_buckets and item.get("applicability") in self.POSITIVE_APPLICABILITY:
                role_buckets[role].append(item)
                eligible_items.append(item)
            elif role == "weak_or_unrelated":
                item["coverage_drop_reason"] = "weak_or_unrelated"
                if self._is_relevant_drop(item):
                    dropped_relevant.append(self._dropped_candidate_summary(item, "weak_or_unrelated"))

        for claim in normalized_claims:
            best = self._best_for_claim(claim, eligible_items, facts, user_text, normalized_claims)
            if best:
                claim_coverage[claim] = {
                    "covered": True,
                    "covered_by": self._summarize_candidate(best),
                    "role": best.get("legal_role"),
                }
                self._try_select(best, selected, selected_ids, "claim_direct")
            else:
                claim_coverage[claim] = {"covered": False, "covered_by": None}

        fact_roles = self._relevant_fact_roles(facts, user_text, ranked_items, normalized_claims)
        for role in fact_roles:
            best = self._best_for_role(role_buckets.get(role, []))
            if not best:
                continue
            role_coverage[role] = self._summarize_candidate(best)
            self._try_select(best, selected, selected_ids, "fact_role")

        supportive_roles = self._supportive_roles(normalized_claims, fact_roles, facts, user_text)
        for role in supportive_roles:
            if len(selected) >= self.MAX_SELECTED_ARTICLES:
                break
            best = self._best_for_role(role_buckets.get(role, []))
            if not best:
                continue
            role_coverage.setdefault(role, self._summarize_candidate(best))
            self._try_select(best, selected, selected_ids, "supportive_role")

        for item in eligible_items:
            if len(selected) >= self.MAX_SELECTED_ARTICLES:
                break
            if str(item.get("id")) in selected_ids:
                continue
            if item.get("applicability") not in self.POSITIVE_APPLICABILITY:
                continue
            if item.get("legal_role") == "weak_or_unrelated":
                continue
            self._try_select(item, selected, selected_ids, "high_relevance_fill")

        if not selected:
            fallback_source = eligible_items if eligible_items else positive_items
            for item in fallback_source[: self.MAX_SELECTED_ARTICLES]:
                self._try_select(item, selected, selected_ids, "fallback_non_empty")

        selected = sorted(selected[: self.MAX_SELECTED_HARD_LIMIT], key=self._sort_key, reverse=True)
        selected_roles = {str(item.get("legal_role") or "") for item in selected if str(item.get("legal_role") or "")}

        missing_claims = [claim for claim, payload in claim_coverage.items() if not payload.get("covered")]
        missing_roles = [role for role in fact_roles if role not in selected_roles]

        for item in ranked_items:
            item_id = str(item.get("id"))
            if item_id in selected_ids:
                continue
            reason = item.get("coverage_drop_reason") or self._default_drop_reason(item, selected, normalized_claims, facts, user_text)
            item["coverage_drop_reason"] = reason
            if self._is_relevant_drop(item):
                dropped_relevant.append(self._dropped_candidate_summary(item, reason))

        coverage = {
            "claims": claim_coverage,
            "detected_legal_roles": list(dict.fromkeys(fact_roles + [str(item.get("legal_role") or "") for item in selected if str(item.get("legal_role") or "")])),
            "selected_article_per_role": {
                role: self._summarize_candidate(self._best_for_role([item for item in selected if item.get("legal_role") == role]))
                for role in self.ROLE_PRIORITY
                if self._best_for_role([item for item in selected if item.get("legal_role") == role])
            },
            "missing_roles": missing_roles,
            "missing_claims": missing_claims,
            "missing_coverage": {"claims": missing_claims, "roles": missing_roles},
            "articles_dropped_despite_high_score": dropped_relevant[: self.MAX_TRACE_DROPPED],
        }
        return selected, coverage

    def _select_by_entailment(
        self,
        user_text: str,
        facts: dict[str, Any],
        normalized_claims: list[str],
        ranked_items: list[dict[str, Any]],
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        coverage = self.entailment_checker.build_coverage(
            normalized_claims,
            ranked_items,
            facts,
            user_text,
            request_id=request_id,
            run_id=run_id,
        )
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        dropped_relevant: list[dict[str, Any]] = []

        valid_entries = [
            entry for entry in coverage.get("coverage_map", [])
            if isinstance(entry, dict) and entry.get("counts_as_covered")
        ]
        supporting_entries = [
            entry for entry in coverage.get("coverage_map", [])
            if isinstance(entry, dict) and entry.get("coverage_type") == "supporting"
        ]
        valid_article_ids = {str(entry.get("article_id") or "") for entry in valid_entries}

        for claim in normalized_claims:
            best = self._best_entailment_article(claim, ranked_items, valid_entries)
            if best:
                self._prepare_user_visible_article(best)
                self._try_select(best, selected, selected_ids, "claim_entailment")

        if selected:
            for entry in supporting_entries:
                if len(selected) >= self.MAX_SELECTED_ARTICLES:
                    break
                article_id = str(entry.get("article_id") or "")
                if not article_id or article_id in selected_ids:
                    continue
                article = self._article_by_id(ranked_items, article_id)
                if not article:
                    continue
                self._prepare_user_visible_article(article)
                self._try_select(article, selected, selected_ids, "supporting_context")

        for article in ranked_items:
            article_id = str(article.get("id") or "")
            if article_id in selected_ids:
                continue
            coverage_type = str(article.get("coverage_type") or "")
            if coverage_type in {"conditional_missing_facts", "no_coverage"}:
                reason = "unmet_trigger_conditions" if coverage_type == "conditional_missing_facts" else "semantic_no_claim_coverage"
                legacy_reason = self._conditional_drop_reason(
                    str(article.get("legal_role") or ""),
                    article,
                    facts,
                    user_text,
                    normalized_claims,
                )
                if legacy_reason:
                    reason = legacy_reason
                article["coverage_drop_reason"] = reason
                if self._is_relevant_drop(article):
                    dropped_relevant.append(self._dropped_candidate_summary(article, reason))
                continue
            if not selected and coverage_type == "supporting":
                article["coverage_drop_reason"] = "supporting_without_direct_basis"
                if self._is_relevant_drop(article):
                    dropped_relevant.append(self._dropped_candidate_summary(article, "supporting_without_direct_basis"))

        if not selected:
            for article in ranked_items:
                if article.get("coverage_type") != "supporting":
                    continue
                self._prepare_user_visible_article(article)
                self._try_select(article, selected, selected_ids, "fallback_supporting_context")
                if selected:
                    break
        if not selected and normalized_claims == ["other"]:
            for article in ranked_items[: self.MAX_SELECTED_ARTICLES]:
                self._prepare_user_visible_article(article)
                self._try_select(article, selected, selected_ids, "fallback_other_claim")

        selected = sorted(selected[: self.MAX_SELECTED_HARD_LIMIT], key=self._sort_key, reverse=True)
        role_coverage: dict[str, dict[str, Any]] = {}
        for article in selected:
            role = str(article.get("legal_role") or "")
            if role and role not in role_coverage:
                role_coverage[role] = self._summarize_candidate(article)
        coverage["detected_legal_roles"] = list(role_coverage.keys())
        coverage["selected_article_per_role"] = role_coverage
        coverage["missing_roles"] = []
        coverage["articles_dropped_despite_high_score"] = dropped_relevant[: self.MAX_TRACE_DROPPED]
        coverage["semantic_article_effects"] = [
            article.get("semantic_analysis") for article in ranked_items if isinstance(article.get("semantic_analysis"), dict)
        ]
        coverage["entailment_coverage"] = coverage.get("coverage_map", [])
        coverage["valid_article_ids"] = sorted(article_id for article_id in valid_article_ids if article_id)
        return selected, coverage

    def _best_entailment_article(
        self,
        claim: str,
        articles: list[dict[str, Any]],
        entries: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        article_ids = {
            str(entry.get("article_id") or "")
            for entry in entries
            if entry.get("claim") == claim and entry.get("counts_as_covered")
        }
        candidates = [article for article in articles if str(article.get("id") or "") in article_ids]
        if not candidates:
            return None
        return sorted(candidates, key=self._sort_key, reverse=True)[0]

    @staticmethod
    def _article_by_id(articles: list[dict[str, Any]], article_id: str) -> dict[str, Any] | None:
        return next((article for article in articles if str(article.get("id") or "") == article_id), None)

    def _prepare_user_visible_article(self, article: dict[str, Any]) -> None:
        entries = [entry for entry in article.get("coverage", []) if isinstance(entry, dict)]
        strongest = next((entry for entry in entries if entry.get("coverage_type") == "direct"), None)
        strongest = strongest or next((entry for entry in entries if entry.get("coverage_type") == "valid_conditional"), None)
        strongest = strongest or next((entry for entry in entries if entry.get("coverage_type") == "supporting"), None)
        coverage_type = str(strongest.get("coverage_type") or article.get("coverage_type") or "supporting") if strongest else str(article.get("coverage_type") or "supporting")
        effect_type = str(strongest.get("effect_type") or "") if strongest else ""
        article["coverage_type"] = coverage_type
        article["applicability"] = "direct" if coverage_type == "direct" else "related" if coverage_type == "valid_conditional" else "weak"
        current_role = str(article.get("legal_role") or "")
        article["legal_role"] = current_role if current_role and current_role != "weak_or_unrelated" else self._role_for_effect(effect_type) or "obligation_basis"
        if strongest and strongest.get("evidence_quote"):
            article["why_relevant"] = str(strongest.get("effect_description") or "").strip() or self._default_why_relevant_for_role(str(article.get("legal_role") or ""))
            article["coverage_evidence_quote"] = strongest.get("evidence_quote")
            article["coverage_trigger_conditions"] = strongest.get("trigger_conditions") or []
        else:
            article["why_relevant"] = self._default_why_relevant_for_role(str(article.get("legal_role") or ""))

    @staticmethod
    def _role_for_effect(effect_type: str) -> str | None:
        return {
            "return_principal": "refund_or_restitution",
            "return_received": "refund_or_restitution",
            "damages_recovery": "damages_recovery",
            "damages_definition": "damages_definition",
            "liability_basis": "liability_basis",
            "interest": "monetary_obligation_interest",
            "interest_recovery": "monetary_obligation_interest",
            "delay_liability": "breach_or_delay",
            "termination_or_refusal": "termination_or_refusal",
            "termination_consequence": "refund_or_restitution",
            "termination_consequences": "refund_or_restitution",
            "obligation_performance": "obligation_basis",
            "performance_terms": "performance_terms",
            "obligation_basis": "obligation_basis",
            "security_or_deposit": "penalty_or_security",
            "penalty": "penalty_or_security",
            "penalty_or_security": "penalty_or_security",
            "limitation_or_exception": "exception_or_limitation",
            "creditor_delay": "exception_or_limitation",
            "impossibility": "exception_or_limitation",
            "procedural": "procedure",
        }.get(effect_type)

    @classmethod
    def _try_select(
        cls,
        item: dict[str, Any],
        selected: list[dict[str, Any]],
        selected_ids: set[str],
        reason: str,
    ) -> None:
        item_id = str(item.get("id"))
        if not item_id or item_id in selected_ids:
            return
        selected_ids.add(item_id)
        item["coverage_selected_reason"] = reason
        selected.append(item)

    def _best_for_claim(
        self,
        claim: str,
        items: list[dict[str, Any]],
        facts: dict[str, Any],
        user_text: str,
        normalized_claims: list[str],
    ) -> dict[str, Any] | None:
        candidates = [
            item
            for item in items
            if self._article_can_cover_claim(item, claim, facts, user_text, normalized_claims)
        ]
        if not candidates:
            return None
        priorities = self.CLAIM_ROLE_PRIORITY.get(claim, ["obligation_basis"])
        return sorted(candidates, key=lambda item: self._claim_sort_key(claim, item, priorities), reverse=True)[0]

    @staticmethod
    def _best_for_role(items: list[dict[str, Any]] | None) -> dict[str, Any] | None:
        if not items:
            return None
        return sorted(items, key=LawReranker._sort_key, reverse=True)[0]

    def _claim_sort_key(self, claim: str, item: dict[str, Any], priorities: list[str]) -> tuple[float, float, float, float]:
        role = str(item.get("legal_role") or "")
        priority_weight = float(len(priorities) - priorities.index(role)) if role in priorities else 0.0
        base = self._sort_key(item)
        claim_direct_bonus = 0.2 if claim in self._list_of_strings(item.get("covers_claims")) else 0.0
        return (priority_weight + claim_direct_bonus, base[0], base[1], base[2])

    @staticmethod
    def _sort_key(item: dict[str, Any]) -> tuple[float, float, float]:
        applicability_weight = 1.0 if item.get("applicability") == "direct" else 0.75 if item.get("applicability") == "related" else 0.5
        score = float(item.get("relevance_score") or item.get("combined_score") or item.get("keyword_score") or item.get("vector_score") or 0.0)
        fact_match = float(item.get("coverage_fact_match") or 0.0)
        penalty = float(item.get("coverage_penalty") or 0.0)
        return (applicability_weight, score + fact_match - penalty, fact_match)

    def _normalize_facts(self, facts: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(facts)
        parties = facts.get("parties") if isinstance(facts.get("parties"), dict) else {}
        transaction = facts.get("transaction") if isinstance(facts.get("transaction"), dict) else {}
        problem = facts.get("problem") if isinstance(facts.get("problem"), dict) else {}
        user_demand = facts.get("user_demand") if isinstance(facts.get("user_demand"), dict) else {}
        prior_contact = facts.get("prior_contact") if isinstance(facts.get("prior_contact"), dict) else {}
        documents = facts.get("documents") if isinstance(facts.get("documents"), dict) else {}
        demand = facts.get("demand") if isinstance(facts.get("demand"), dict) else {}

        normalized["parties"] = {
            "claimant_role": parties.get("claimant_role") or parties.get("applicant_role"),
            "opponent_role": parties.get("opponent_role"),
            "opponent_response": parties.get("opponent_response") or prior_contact.get("opponent_response"),
        }
        normalized["transaction"] = {
            "type": transaction.get("type") or transaction.get("item_or_service"),
            "item_or_service": transaction.get("item_or_service") or transaction.get("type"),
            "price": transaction.get("price") or transaction.get("price_amount"),
            "price_amount": transaction.get("price_amount") or transaction.get("price"),
            "date": self._first_present(
                transaction.get("date"),
                (transaction.get("purchase_or_order_date") or {}).get("exact_date") if isinstance(transaction.get("purchase_or_order_date"), dict) else None,
                (transaction.get("purchase_or_order_date") or {}).get("raw_text") if isinstance(transaction.get("purchase_or_order_date"), dict) else None,
            ),
            "payment_confirmed": self._yes_no_to_bool(self._first_present(transaction.get("payment_confirmed"), documents.get("receipt"))),
            "contract_present": self._yes_no_to_bool(self._first_present(transaction.get("contract_present"), documents.get("contract"))),
        }
        normalized["problem"] = {
            "type": problem.get("type") or problem.get("problem_type"),
            "description": problem.get("description"),
            "deadline": self._first_present(problem.get("deadline"), (problem.get("problem_date") or {}).get("raw_text") if isinstance(problem.get("problem_date"), dict) else None),
            "violation_date": self._first_present(
                problem.get("violation_date"),
                (problem.get("problem_date") or {}).get("exact_date") if isinstance(problem.get("problem_date"), dict) else None,
                (problem.get("problem_date") or {}).get("raw_text") if isinstance(problem.get("problem_date"), dict) else None,
            ),
        }
        normalized["demand"] = {
            "type": demand.get("type") or user_demand.get("demand_type"),
            "amount": demand.get("amount") or user_demand.get("amount"),
            "requested_at": self._first_present(
                demand.get("requested_at"),
                (prior_contact.get("contact_date") or {}).get("exact_date") if isinstance(prior_contact.get("contact_date"), dict) else None,
                (prior_contact.get("contact_date") or {}).get("raw_text") if isinstance(prior_contact.get("contact_date"), dict) else None,
            ),
        }
        normalized["documents"] = documents
        normalized["normalized_claims"] = self._normalize_existing_claims(
            facts.get("normalized_claims") if isinstance(facts.get("normalized_claims"), list) else []
        )
        normalized["derived_flags"] = {
            "has_contract": bool(normalized["transaction"]["contract_present"]),
            "has_payment": bool(normalized["transaction"]["payment_confirmed"] or normalized["transaction"]["price_amount"]),
            "has_dates": bool(normalized["transaction"]["date"] or normalized["problem"]["deadline"] or normalized["problem"]["violation_date"]),
            "has_opponent_response": bool(normalized["parties"]["opponent_response"]),
        }
        return normalized

    def _normalized_claims(self, facts: dict[str, Any], user_text: str) -> list[str]:
        existing = facts.get("normalized_claims")
        if isinstance(existing, list) and existing:
            return self._normalize_existing_claims(existing)

        text = self._facts_text(facts, user_text)
        claims: list[str] = []
        demand_type = str((facts.get("demand") or {}).get("type") if isinstance(facts.get("demand"), dict) else "").lower()
        if demand_type == "refund" or any(token in text for token in ("возврат", "вернуть деньги", "возвратить деньги")):
            claims.append("refund_principal")
        if any(token in text for token in ("процент", "ст. 395", "пользовани чуж", "неправомерн удержан")):
            claims.append("interest")
        if demand_type == "compensation" or any(token in text for token in ("убыт", "компенсац", "возмест")):
            claims.append("damages")
        if demand_type == "perform_service" or any(token in text for token in ("исполнить", "выполнить", "оказать услугу")):
            claims.append("performance")
        if demand_type == "cancel_contract" or any(token in text for token in ("расторг", "отказ от договора", "отказаться от договора")):
            claims.append("termination/refusal")
        has_penalty_terms = any(token in text for token in ("неустой", "штраф", "пен", "задат", "обеспеч"))
        has_penalty_negation = any(
            token in text
            for token in (
                "без неустой",
                "без штраф",
                "без пен",
                "без задат",
                "без обеспеч",
            )
        )
        if has_penalty_terms and not has_penalty_negation:
            claims.append("penalty")
        if any(token in text for token in ("реституц", "неосновательн", "возврат уплаченного")):
            claims.append("restitution")
        if not claims:
            claims.append("other")
        return self._normalize_existing_claims(claims)

    @classmethod
    def _normalize_existing_claims(cls, claims: list[Any]) -> list[str]:
        normalized: list[str] = []
        for claim in claims:
            if not isinstance(claim, str):
                continue
            value = claim.strip().lower()
            if value == "refund":
                value = "refund_principal"
            if value == "termination_or_refusal":
                value = "termination/refusal"
            if value not in normalized:
                normalized.append(value)
        return normalized

    @classmethod
    def _normalize_role(cls, role: Any) -> str | None:
        if not isinstance(role, str):
            return None
        normalized = role.strip().lower()
        return normalized if normalized in cls.VALID_ROLES else None

    @classmethod
    def _normalize_applicability(cls, applicability: Any) -> str | None:
        if not isinstance(applicability, str):
            return None
        normalized = applicability.strip().lower()
        if normalized == "indirect":
            return "related"
        if normalized == "conditional":
            return "related"
        return normalized

    @classmethod
    def _infer_role(cls, item: dict[str, Any]) -> str:
        haystack = " ".join(
            [
                str(item.get("article_title") or ""),
                str(item.get("regulates") or ""),
                str(item.get("why_relevant") or ""),
                str(item.get("article_text") or ""),
            ]
        ).lower()
        if "процент" in haystack or "удержан" in haystack or "денежн" in haystack and "пользован" in haystack:
            return "monetary_obligation_interest"
        if "возврат" in haystack or "реституц" in haystack or "неосновательн" in haystack:
            return "refund_or_restitution"
        if "расторжен" in haystack or "изменен" in haystack or "отказ от договора" in haystack or "защита гражданских прав" in haystack:
            return "termination_or_refusal"
        if "обязан возмест" in haystack or "обязанность должника" in haystack or "ответствен" in haystack:
            return "liability_basis"
        if "убыт" in haystack and ("право которого нарушено" in haystack or "реальный ущерб" in haystack or "упущенн" in haystack):
            return "damages_definition"
        if "убыт" in haystack and ("возмест" in haystack or "взыск" in haystack):
            return "damages_recovery"
        if "нарушен" in haystack or "неисполн" in haystack or "просроч" in haystack or "задерж" in haystack:
            return "breach_or_delay"
        if "исполн" in haystack or "срок исполн" in haystack or "надлежащ" in haystack or "односторонн" in haystack:
            return "performance_terms"
        if "неустой" in haystack or "штраф" in haystack or "пен" in haystack or "задат" in haystack or "обеспеч" in haystack or "поручитель" in haystack or "залог" in haystack:
            return "penalty_or_security"
        if "исков" in haystack or "давност" in haystack or "невозможност" in haystack or "освобожд" in haystack or "ограничен ответствен" in haystack:
            return "exception_or_limitation"
        if "порядок" in haystack or "процедур" in haystack or "претензи" in haystack or "досудеб" in haystack or "судебн" in haystack:
            return "procedure"
        if "обязатель" in haystack or "договор" in haystack or "возникнов" in haystack or "правоотнош" in haystack:
            return "obligation_basis"
        return "weak_or_unrelated"

    @staticmethod
    def _fallback_applicability(score: float) -> str:
        if score >= 0.6:
            return "direct"
        if score >= 0.35:
            return "related"
        if score > 0:
            return "weak"
        return "not_applicable"

    def _fact_match_score(self, item: dict[str, Any], facts: dict[str, Any], user_text: str) -> float:
        text = self._facts_text(facts, user_text)
        role = str(item.get("legal_role") or "")
        score = 0.0
        if role == "obligation_basis" and any(token in text for token in ("договор", "обязатель")):
            score += 0.18
        if role == "performance_terms" and any(token in text for token in ("срок", "исполн", "надлежащ")):
            score += 0.18
        if role == "breach_or_delay" and any(token in text for token in ("наруш", "неисполн", "просроч", "задерж")):
            score += 0.18
        if role == "liability_basis" and any(token in text for token in ("ответствен", "возмест")):
            score += 0.18
        if role == "damages_definition" and any(token in text for token in ("убыт", "ущерб", "упущенн")):
            score += 0.18
        if role == "damages_recovery" and any(token in text for token in ("взыск", "возмест", "убыт")):
            score += 0.18
        if role == "refund_or_restitution" and any(token in text for token in ("возврат", "вернул", "деньги")):
            score += 0.18
        if role == "termination_or_refusal" and any(token in text for token in ("расторж", "отказ")):
            score += 0.16
        if role == "monetary_obligation_interest" and self._has_money_retention(facts, user_text, self._normalized_claims(facts, user_text)):
            score += 0.16
        if role == "procedure" and any(token in text for token in ("претенз", "досудеб", "суд")):
            score += 0.12
        if role == "penalty_or_security" and self._has_penalty_or_security_facts(facts, user_text):
            score += 0.12
        if role == "exception_or_limitation" and self._has_exception_or_limitation_facts(facts, user_text):
            score += 0.12
        return score

    @staticmethod
    def _missing_facts_penalty(item: dict[str, Any]) -> float:
        missing = item.get("missing_facts")
        return min(len(missing) * 0.03, 0.15) if isinstance(missing, list) else 0.0

    def _infer_missing_facts(self, item: dict[str, Any], facts: dict[str, Any]) -> list[str]:
        role = self._normalize_role(item.get("legal_role")) or self._infer_role(item)
        transaction = facts.get("transaction") if isinstance(facts.get("transaction"), dict) else {}
        problem = facts.get("problem") if isinstance(facts.get("problem"), dict) else {}
        demand = facts.get("demand") if isinstance(facts.get("demand"), dict) else {}
        parties = facts.get("parties") if isinstance(facts.get("parties"), dict) else {}
        missing: list[str] = []
        if role == "performance_terms" and not problem.get("deadline"):
            missing.append("срок исполнения")
        if role in {"refund_or_restitution", "termination_or_refusal", "monetary_obligation_interest"} and not parties.get("opponent_response"):
            missing.append("реакция второй стороны на требование")
        if role in {"damages_definition", "damages_recovery"} and not demand.get("amount"):
            missing.append("размер требований")
        if role == "obligation_basis" and not (transaction.get("contract_present") or transaction.get("item_or_service")):
            missing.append("наличие договора или предмет обязательства")
        return missing

    def _conditional_drop_reason(
        self,
        role: str,
        item: dict[str, Any],
        facts: dict[str, Any],
        user_text: str,
        normalized_claims: list[str],
    ) -> str | None:
        if item.get("applicability") == "not_applicable":
            return "llm_marked_not_applicable"
        if role == "penalty_or_security" and not self._has_penalty_or_security_facts(facts, user_text):
            return "conditional_role_not_confirmed"
        if role == "exception_or_limitation" and not self._has_exception_or_limitation_facts(facts, user_text):
            return "conditional_role_not_confirmed"
        if not bool(item.get("registry_conditions_met", True)):
            return "registry_condition_not_confirmed"
        if role == "monetary_obligation_interest" and "interest" not in normalized_claims and not self._has_money_retention(facts, user_text, normalized_claims):
            return "conditional_role_not_confirmed"
        return None

    def _has_penalty_or_security_facts(self, facts: dict[str, Any], user_text: str) -> bool:
        text = self._facts_text(facts, user_text)
        if any(token in text for token in ("без неустой", "нет неустой", "без штраф", "без пен", "без задат")):
            return False
        return any(token in text for token in ("неустой", "штраф", "пен", "задат", "обеспеч", "поручитель", "залог"))

    def _has_exception_or_limitation_facts(self, facts: dict[str, Any], user_text: str) -> bool:
        text = self._facts_text(facts, user_text)
        if any(token in text for token in ("без ссылки на давност", "без давност", "нет давност", "не заявлял давност")):
            return False
        return any(token in text for token in ("исков", "давност", "невозможност", "освобожд", "ограничен ответствен", "недействитель", "вина кредитора"))

    def _has_missed_deadline_or_delay(self, facts: dict[str, Any], user_text: str) -> bool:
        text = self._facts_text(facts, user_text)
        problem = facts.get("problem") if isinstance(facts.get("problem"), dict) else {}
        has_dates = bool(problem.get("deadline") or problem.get("violation_date"))
        mentions_delay = any(token in text for token in ("срок", "просроч", "не исполнил", "неисполн", "задерж"))
        explicit_delay = any(token in text for token in ("просроч", "неисполн", "в срок", "истек срок"))
        return (has_dates and (mentions_delay or str(problem.get("type") or "").lower() == "nonperformance")) or explicit_delay

    def _has_settlement_in_lieu(self, facts: dict[str, Any], user_text: str) -> bool:
        text = self._facts_text(facts, user_text)
        if any(token in text for token in ("без отступного", "нет отступного")):
            return False
        return "отступн" in text

    def _has_termination_or_refusal_basis(self, facts: dict[str, Any], user_text: str, normalized_claims: list[str]) -> bool:
        text = self._facts_text(facts, user_text)
        demand = facts.get("demand") if isinstance(facts.get("demand"), dict) else {}
        return (
            "termination/refusal" in normalized_claims
            or str(demand.get("type") or "").lower() == "cancel_contract"
            or any(token in text for token in ("расторг", "отказ от договора", "отказаться от договора"))
        )

    def _has_money_retention(self, facts: dict[str, Any], user_text: str, normalized_claims: list[str]) -> bool:
        text = self._facts_text(facts, user_text)
        parties = facts.get("parties") if isinstance(facts.get("parties"), dict) else {}
        demand = facts.get("demand") if isinstance(facts.get("demand"), dict) else {}
        transaction = facts.get("transaction") if isinstance(facts.get("transaction"), dict) else {}
        return (
            ("refund_principal" in normalized_claims or str(demand.get("type") or "").lower() == "refund")
            and bool(parties.get("opponent_response"))
            and bool(transaction.get("price_amount") or transaction.get("payment_confirmed") or demand.get("requested_at"))
        ) or any(token in text for token in ("удерж", "не возвращает деньги", "не вернул деньги"))

    def _conditions_met(
        self,
        rule: ArticleRoleRule | None,
        facts: dict[str, Any],
        user_text: str,
        normalized_claims: list[str],
    ) -> bool:
        return not self._failed_conditions(rule, facts, user_text, normalized_claims)

    def _failed_conditions(
        self,
        rule: ArticleRoleRule | None,
        facts: dict[str, Any],
        user_text: str,
        normalized_claims: list[str],
    ) -> list[str]:
        if not rule:
            return []
        failed: list[str] = []
        for condition in rule.conditions:
            if condition == "has_money_retention" and not self._has_money_retention(facts, user_text, normalized_claims):
                failed.append(condition)
            elif condition == "has_missed_deadline_or_delay" and not self._has_missed_deadline_or_delay(facts, user_text):
                failed.append(condition)
            elif condition == "has_settlement_in_lieu" and not self._has_settlement_in_lieu(facts, user_text):
                failed.append(condition)
            elif condition == "has_termination_or_refusal_basis" and not self._has_termination_or_refusal_basis(facts, user_text, normalized_claims):
                failed.append(condition)
        return failed

    def _article_can_cover_claim(
        self,
        item: dict[str, Any],
        claim: str,
        facts: dict[str, Any],
        user_text: str,
        normalized_claims: list[str],
    ) -> bool:
        cannot_cover = set(self._list_of_strings(item.get("cannot_cover_claims")))
        if claim in cannot_cover:
            return False
        covers_claims = self._list_of_strings(item.get("covers_claims"))
        if claim not in covers_claims:
            return False
        rule = self.role_registry.lookup(item.get("act_name"), item.get("article_number"))
        if not self._conditions_met(rule, facts, user_text, normalized_claims):
            return False
        return item.get("applicability") in self.POSITIVE_APPLICABILITY

    def _relevant_fact_roles(
        self,
        facts: dict[str, Any],
        user_text: str,
        ranked_items: list[dict[str, Any]],
        normalized_claims: list[str],
    ) -> list[str]:
        available = {
            str(item.get("legal_role") or "")
            for item in ranked_items
            if str(item.get("legal_role") or "") in self.ROLE_PRIORITY
        }
        roles: list[str] = []
        if "obligation_basis" in available and (facts.get("derived_flags") or {}).get("has_contract"):
            roles.append("obligation_basis")
        if "performance_terms" in available and ((facts.get("problem") or {}).get("deadline") or "срок" in self._facts_text(facts, user_text)):
            roles.append("performance_terms")
        if "breach_or_delay" in available and self._has_missed_deadline_or_delay(facts, user_text):
            roles.append("breach_or_delay")
        if "liability_basis" in available and any(claim in normalized_claims for claim in ("damages", "penalty")):
            roles.append("liability_basis")
        if "monetary_obligation_interest" in available and ("interest" in normalized_claims or self._has_money_retention(facts, user_text, normalized_claims)):
            roles.append("monetary_obligation_interest")
        if "procedure" in available and ((facts.get("parties") or {}).get("opponent_response") or "претенз" in self._facts_text(facts, user_text)):
            roles.append("procedure")
        return roles

    def _supportive_roles(self, normalized_claims: list[str], fact_roles: list[str], facts: dict[str, Any], user_text: str) -> list[str]:
        roles: list[str] = []
        for claim in normalized_claims:
            roles.extend(self.CLAIM_ROLE_PRIORITY.get(claim, []))
        roles.extend(fact_roles)
        if self._has_money_retention(facts, user_text, normalized_claims):
            roles.append("monetary_obligation_interest")
        deduped: list[str] = []
        for role in roles:
            if role in self.ROLE_PRIORITY and role not in deduped:
                deduped.append(role)
        return deduped

    @staticmethod
    def _facts_text(facts: dict[str, Any], user_text: str) -> str:
        parts = [str(user_text or ""), str(facts.get("summary") or "")]
        for key in ("transaction", "problem", "demand", "parties", "documents"):
            value = facts.get(key)
            if isinstance(value, dict):
                parts.extend(str(item) for item in value.values() if item)
        return " ".join(parts).lower()

    @staticmethod
    def _first_present(*values: Any) -> Any:
        for value in values:
            if value not in (None, "", "unknown"):
                return value
        return None

    @staticmethod
    def _yes_no_to_bool(value: Any) -> bool | None:
        if value in (True, False):
            return value
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        if normalized == "yes":
            return True
        if normalized == "no":
            return False
        return None

    @classmethod
    def _is_relevant_drop(cls, item: dict[str, Any]) -> bool:
        score = float(item.get("relevance_score") or item.get("combined_score") or item.get("keyword_score") or item.get("vector_score") or 0.0)
        applicability = str(item.get("applicability") or "")
        return score >= cls.HIGH_SCORE_THRESHOLD or applicability in {"direct", "related"}

    def _default_drop_reason(
        self,
        item: dict[str, Any],
        selected: list[dict[str, Any]],
        normalized_claims: list[str],
        facts: dict[str, Any],
        user_text: str,
    ) -> str:
        role = str(item.get("legal_role") or "")
        selected_roles = {str(selected_item.get("legal_role") or "") for selected_item in selected}
        if any(self._article_can_cover_claim(item, claim, facts, user_text, normalized_claims) for claim in normalized_claims):
            if role in selected_roles:
                return "role_already_covered_by_stronger_article"
            return "higher_priority_claim_coverage_selected"
        if role in selected_roles:
            return "role_already_covered_by_stronger_article"
        return "lower_priority_than_selected_articles"

    def _dropped_candidate_summary(self, item: dict[str, Any], reason: str) -> dict[str, Any]:
        summary = self._summarize_candidate(item)
        summary["reason"] = reason
        summary["legal_role"] = item.get("legal_role")
        return summary

    @staticmethod
    def _to_prompt_json(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str)

    @classmethod
    def _build_trace(
        cls,
        candidates_before: list[dict[str, Any]],
        facts: dict[str, Any],
        normalized_claims: list[str],
        ranked_items: list[dict[str, Any]],
        selected_items: list[dict[str, Any]],
        coverage: dict[str, Any],
        role_corrections: list[dict[str, Any]],
        *,
        ranked_candidates: list[dict[str, Any]],
        llm_candidates: list[dict[str, Any]],
        rerank_query: str,
        rerank_fallback: bool,
    ) -> dict[str, Any]:
        before_by_id = {str(item.get("id")): item for item in candidates_before}
        selected_by_id = {str(item.get("id")): item for item in selected_items}
        ranked_by_id = {str(item.get("id")): item for item in ranked_candidates}
        promoted: list[dict[str, Any]] = []
        demoted: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []

        for article_id, before in before_by_id.items():
            after = selected_by_id.get(article_id)
            ranked = ranked_by_id.get(article_id)
            if not after:
                if ranked:
                    dropped.append(cls._summarize_candidate(ranked))
                continue
            before_score = float(before.get("combined_score") or before.get("keyword_score") or before.get("vector_score") or 0.0)
            after_score = float(after.get("relevance_score") or 0.0)
            if after_score >= before_score + 0.05:
                promoted.append(cls._summarize_candidate(after))
            if after_score + 0.05 < before_score or after.get("applicability") == "not_applicable":
                demoted.append(cls._summarize_candidate(after))

        return {
            "facts_for_reranker": facts,
            "normalized_claims": normalized_claims,
            "rerank_query": rerank_query,
            "rerank_fallback": rerank_fallback,
            "candidates_before": [cls._summarize_candidate(item) for item in candidates_before],
            "reranker_output": [cls._summarize_candidate(item) for item in ranked_candidates],
            "llm_articles_selected": [cls._summarize_candidate(item) for item in llm_candidates],
            "candidates_after": [cls._summarize_candidate(item) for item in selected_items],
            "promoted_articles": promoted,
            "demoted_articles": demoted,
            "dropped_articles": dropped,
            "coverage": coverage,
            "semantic_article_effects": coverage.get("semantic_article_effects", []),
            "entailment_coverage": coverage.get("entailment_coverage", []),
            "role_corrections": role_corrections,
            "dropped_relevant_candidates": coverage.get("articles_dropped_despite_high_score", []),
        }

    @staticmethod
    def _summarize_candidate(candidate: dict[str, Any] | None) -> dict[str, Any]:
        if not candidate:
            return {}
        return {
            "id": str(candidate.get("id") or ""),
            "act_name": candidate.get("act_name"),
            "act_code": candidate.get("act_code"),
            "article_number": candidate.get("article_number"),
            "title": candidate.get("article_title"),
            "score": float(candidate.get("relevance_score") or candidate.get("combined_score") or candidate.get("keyword_score") or candidate.get("vector_score") or 0.0),
            "hybrid_score": float(candidate.get("hybrid_score") or candidate.get("combined_score") or candidate.get("keyword_score") or candidate.get("vector_score") or 0.0),
            "rerank_score": float(candidate.get("rerank_score") or candidate.get("relevance_score") or 0.0),
            "applicability": candidate.get("applicability"),
            "why_relevant": candidate.get("why_relevant"),
            "legal_role": candidate.get("legal_role"),
            "covers_claims": candidate.get("covers_claims"),
            "coverage_type": candidate.get("coverage_type"),
            "boost_reason": candidate.get("boost_reason"),
            "selection_reason": candidate.get("selection_reason"),
            "rerank_reason": candidate.get("rerank_reason"),
            "pre_llm_reason": candidate.get("pre_llm_reason"),
            "matched_negative_domain": candidate.get("matched_negative_domain"),
            "matched_negative_keywords": candidate.get("matched_negative_keywords"),
            "matched_positive_signals": candidate.get("matched_positive_signals"),
            "negative_overridden_by_positive": candidate.get("negative_overridden_by_positive"),
            "negative_domain_decision_reason": candidate.get("negative_domain_decision_reason"),
        }

    def _user_visible_why_relevant(self, item: dict[str, Any], rule: ArticleRoleRule | None) -> str:
        why_relevant = str(item.get("why_relevant") or "").strip()
        if why_relevant and all(marker not in why_relevant for marker in self.INTERNAL_WHY_RELEVANT_MARKERS):
            return why_relevant
        if rule and rule.user_visible_default:
            return rule.user_visible_default
        role = str(item.get("legal_role") or "")
        return self._default_why_relevant_for_role(role)

    @staticmethod
    def _default_why_relevant_for_role(role: str) -> str:
        defaults = {
            "obligation_basis": "Статья задает базовый правовой режим обязательства по этой ситуации.",
            "performance_terms": "Статья регулирует условия и срок исполнения обязательства по спору.",
            "breach_or_delay": "Статья относится к нарушению или просрочке исполнения обязательства.",
            "liability_basis": "Статья описывает общее основание гражданско-правовой ответственности.",
            "damages_definition": "Статья раскрывает понятие и состав убытков, которые можно заявлять.",
            "damages_recovery": "Статья прямо поддерживает требование о взыскании убытков.",
            "refund_or_restitution": "Статья связана с возвратом полученного или денежных средств при прекращении обязательства.",
            "termination_or_refusal": "Статья относится к расторжению договора или отказу от него.",
            "monetary_obligation_interest": "Статья регулирует проценты за неправомерное удержание денежных средств.",
            "procedure": "Статья помогает описать порядок защиты права или предъявления требований.",
            "penalty_or_security": "Статья регулирует неустойку или обеспечительные механизмы по обязательству.",
            "exception_or_limitation": "Статья содержит ограничения или исключения, влияющие на спор.",
        }
        return defaults.get(role, "Статья относится к подтвержденному правовому контексту по этой ситуации.")

    @staticmethod
    def _claims_for_role(role: str) -> list[str]:
        mapping = {
            "damages_definition": ["damages"],
            "damages_recovery": ["damages"],
            "refund_or_restitution": ["refund_principal", "restitution"],
            "termination_or_refusal": ["termination/refusal"],
            "monetary_obligation_interest": ["interest"],
        }
        return mapping.get(role, [])

    @staticmethod
    def _list_of_strings(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, str)]
