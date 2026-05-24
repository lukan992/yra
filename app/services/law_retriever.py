import time
from typing import Any

from app.schemas.pipeline import LegalContextNotFoundError
from app.services.hybrid_law_retriever import HybridLawRetriever
from app.services.law_reranker import LawReranker


class LawRetriever:
    def __init__(self, hybrid_retriever: HybridLawRetriever, law_reranker: LawReranker) -> None:
        self.hybrid_retriever = hybrid_retriever
        self.law_reranker = law_reranker
        self.last_trace: dict[str, float] = {}

    def retrieve(
        self,
        user_text: str,
        facts: dict[str, Any],
        legal_area: dict[str, Any],
        query_payload: dict[str, Any],
        top_k: int = 8,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        started_at = time.perf_counter()
        candidates = self.hybrid_retriever.retrieve(query_payload, limit=max(top_k * 6, 30))
        retrieval_duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
        if not candidates:
            raise LegalContextNotFoundError(
                "Не удалось подобрать достаточно релевантные нормы по текущему описанию. Нужны дополнительные факты или более конкретная формулировка спора.",
                {"query_payload": query_payload},
            )
        rerank_started_at = time.perf_counter()
        reranked = self.law_reranker.rerank(user_text, facts, legal_area, candidates)
        rerank_duration_ms = round((time.perf_counter() - rerank_started_at) * 1000, 3)
        repair_trace: dict[str, Any] = {}
        missing_claims = self._missing_claims()
        if missing_claims:
            repair_started_at = time.perf_counter()
            repair_payload = self._build_repair_query(query_payload, facts, legal_area, missing_claims)
            repair_candidates = self.hybrid_retriever.retrieve(repair_payload, limit=max(top_k * 4, 24))
            merged_candidates = self._merge_candidates(candidates, repair_candidates)
            rerank_started_at = time.perf_counter()
            reranked = self.law_reranker.rerank(user_text, facts, legal_area, merged_candidates)
            rerank_duration_ms += round((time.perf_counter() - rerank_started_at) * 1000, 3)
            repair_trace = {
                "started": True,
                "duration_ms": round((time.perf_counter() - repair_started_at) * 1000, 3),
                "missing_claims": missing_claims,
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
                "Найденных норм пока недостаточно для уверенного вывода. Нужны дополнительные факты или уточнение ситуации.",
                {"query_payload": query_payload, "candidate_ids": [item["id"] for item in candidates]},
            )
        return candidates, reranked[:top_k]

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
