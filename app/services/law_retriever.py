import time
from typing import Any

from app.core.config import get_settings
from app.schemas.pipeline import LegalContextNotFoundError, QueryBuilderError
from app.services.hybrid_law_retriever import HybridLawRetriever
from app.services.law_reranker import LawReranker


class LawRetriever:
    def __init__(self, hybrid_retriever: HybridLawRetriever, law_reranker: LawReranker) -> None:
        self.hybrid_retriever = hybrid_retriever
        self.law_reranker = law_reranker
        self.settings = get_settings()
        self.last_trace: dict[str, float] = {}

    def retrieve(
        self,
        user_text: str,
        facts: dict[str, Any],
        legal_area: dict[str, Any],
        query_payload: dict[str, Any],
        top_k: int = 8,
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        legal_query = str(query_payload.get("legal_query") or query_payload.get("plain_problem") or "").strip()
        keywords = query_payload.get("keywords") if isinstance(query_payload.get("keywords"), list) else []
        if not legal_query or not keywords:
            raise QueryBuilderError(details={"query_payload": query_payload})
        started_at = time.perf_counter()
        retrieval_top_k = max(top_k, int(self.settings.legal_rag_retrieval_top_k))
        final_top_k = max(1, int(self.settings.legal_rag_llm_article_top_k or self.settings.legal_rag_rerank_top_k or top_k))
        candidates = self.hybrid_retriever.retrieve(query_payload, limit=retrieval_top_k)
        retrieval_duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
        if not candidates:
            raise LegalContextNotFoundError(
                "Не удалось подобрать релевантные нормы права по текущему описанию после всех retrieval-попыток.",
                {
                    "query_payload": query_payload,
                    "retriever_trace": getattr(self.hybrid_retriever, "last_trace", {}),
                    "reason": "missing_legal_context",
                },
            )
        rerank_started_at = time.perf_counter()
        try:
            reranked = self.law_reranker.rerank(user_text, facts, legal_area, candidates, request_id=request_id, run_id=run_id)
        except TypeError:
            reranked = self.law_reranker.rerank(user_text, facts, legal_area, candidates)
        rerank_duration_ms = round((time.perf_counter() - rerank_started_at) * 1000, 3)
        repair_trace: dict[str, Any] = {}
        missing_claims = self._missing_claims()
        rerank_fallback = bool(getattr(self.law_reranker, "last_trace", {}).get("rerank_fallback"))
        should_run_repair = bool(missing_claims) or (rerank_fallback and len(reranked) < 2)
        if should_run_repair:
            repair_started_at = time.perf_counter()
            repair_payload = self._build_repair_query(query_payload, facts, legal_area, missing_claims or ["refund_principal"])
            repair_candidates = self.hybrid_retriever.retrieve(repair_payload, limit=retrieval_top_k)
            merged_candidates = self._merge_candidates(candidates, repair_candidates)
            rerank_started_at = time.perf_counter()
            try:
                reranked = self.law_reranker.rerank(
                    user_text,
                    facts,
                    legal_area,
                    merged_candidates,
                    request_id=request_id,
                    run_id=run_id,
                )
            except TypeError:
                reranked = self.law_reranker.rerank(user_text, facts, legal_area, merged_candidates)
            rerank_duration_ms += round((time.perf_counter() - rerank_started_at) * 1000, 3)
            repair_trace = {
                "started": True,
                "duration_ms": round((time.perf_counter() - repair_started_at) * 1000, 3),
                "missing_claims": missing_claims,
                "rerank_fallback": rerank_fallback,
                "query_payload": repair_payload,
                "candidate_count": len(repair_candidates),
                "result_ids": [str(item.get("id") or "") for item in repair_candidates],
            }
            candidates = merged_candidates
        self.last_trace = {
            "hybrid_retrieval_duration_ms": retrieval_duration_ms,
            "law_reranking_duration_ms": rerank_duration_ms,
            "repair": repair_trace,
        }
        if not reranked:
            raise LegalContextNotFoundError(
                "Найденных норм пока недостаточно для уверенного правового вывода после semantic coverage-проверки.",
                {
                    "query_payload": query_payload,
                    "candidate_ids": [item["id"] for item in candidates],
                    "reason": "missing_legal_context",
                },
            )
        return candidates, reranked[:final_top_k]

    def _missing_claims(self) -> list[str]:
        last_trace = getattr(self.law_reranker, "last_trace", {})
        coverage = last_trace.get("coverage") if isinstance(last_trace, dict) else {}
        missing = coverage.get("missing_claims") if isinstance(coverage, dict) else []
        return [str(item) for item in missing if isinstance(item, str)] if isinstance(missing, list) else []

    @staticmethod
    def _merge_candidates(base: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in base + extra:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            current = merged.setdefault(item_id, dict(item))
            current["keyword_score"] = max(float(current.get("keyword_score") or 0.0), float(item.get("keyword_score") or 0.0))
            current["vector_score"] = max(float(current.get("vector_score") or 0.0), float(item.get("vector_score") or 0.0))
            current["combined_score"] = max(float(current.get("combined_score") or 0.0), float(item.get("combined_score") or 0.0))
        return sorted(merged.values(), key=lambda row: float(row.get("combined_score") or 0.0), reverse=True)

    @staticmethod
    def _build_repair_query(
        query_payload: dict[str, Any],
        facts: dict[str, Any],
        legal_area: dict[str, Any],
        missing_claims: list[str],
    ) -> dict[str, Any]:
        claim_descriptions = {
            "refund_principal": "возврат уплаченных денежных средств полученного по договору",
            "damages": "возмещение убытков при неисполнении или ненадлежащем исполнении обязательства",
            "interest": "проценты за неправомерное удержание денежных средств или просрочку возврата",
            "performance": "исполнение обязательства надлежащим образом срок исполнения",
            "termination/refusal": "расторжение договора отказ от исполнения договора последствия прекращения",
            "restitution": "возврат полученного реституция неосновательное обогащение",
        }
        facts_terms = LawRetriever._facts_terms(facts, legal_area)
        repair_terms = [claim_descriptions.get(claim, claim) for claim in missing_claims]
        case_type = str(facts.get("preliminary_case_type") or "").lower()
        transaction = facts.get("transaction") if isinstance(facts.get("transaction"), dict) else {}
        legal_area_secondary = legal_area.get("secondary_areas") if isinstance(legal_area.get("secondary_areas"), list) else []
        if case_type == "contract_nonperformance":
            repair_terms.extend(
                [
                    "договор оказания услуг",
                    "возмездное оказание услуг",
                    "неисполнение обязательства",
                    "возврат оплаты",
                    "отказ от договора",
                    "нарушение срока оказания услуги",
                ]
            )
        if any(str(item).lower() == "consumer" for item in legal_area_secondary):
            repair_terms.extend(
                [
                    "защита прав потребителей",
                    "потребитель",
                    "возврат уплаченной суммы потребителю",
                    "убытки потребителя",
                ]
            )
        transaction_type = str(transaction.get("type") or transaction.get("item_or_service") or "").strip()
        if transaction_type:
            repair_terms.append(transaction_type)
        return {
            **query_payload,
            "plain_problem": " ".join(repair_terms + facts_terms),
            "legal_query": " ".join(repair_terms + facts_terms),
            "keywords": list(dict.fromkeys([*query_payload.get("keywords", []), *repair_terms, *facts_terms])),
        }

    @staticmethod
    def _facts_terms(facts: dict[str, Any], legal_area: dict[str, Any]) -> list[str]:
        terms: list[str] = []
        summary = str(facts.get("summary") or "").strip()
        if summary:
            terms.append(summary)
        terms.append(str(legal_area.get("primary_area") or ""))
        transaction = facts.get("transaction") if isinstance(facts.get("transaction"), dict) else {}
        problem = facts.get("problem") if isinstance(facts.get("problem"), dict) else {}
        demand = facts.get("demand") if isinstance(facts.get("demand"), dict) else {}
        for value in (
            transaction.get("type"),
            transaction.get("item_or_service"),
            problem.get("type"),
            problem.get("description"),
            demand.get("type"),
        ):
            if value:
                terms.append(str(value))
        return [term for term in terms if term]
