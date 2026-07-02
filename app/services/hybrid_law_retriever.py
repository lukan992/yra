from typing import Any

from app.core.config import get_settings
from app.repositories.law_repository import LawRepository
from app.schemas.pipeline import EmbeddingUnavailableError
from app.services.embedding_service import EmbeddingService


class HybridLawRetriever:
    ACT_NAME_ALIASES = {
        "гк": "ГК РФ",
        "гк рф": "ГК РФ",
        "гражданский кодекс": "ГК РФ",
        "гражданский кодекс рф": "ГК РФ",
        "гражданский кодекс российской федерации": "ГК РФ",
    }
    ACT_TYPE_ALIASES = {
        "кодекс": "code",
        "code": "code",
        "кодекс рф": "code",
        "гражданский кодекс": "code",
        "гражданское законодательство": "code",
        "гражданское право": "code",
        "законодательство о защите прав потребителей": "law",
    }

    def __init__(self, law_repository: LawRepository, embedding_service: EmbeddingService) -> None:
        self.law_repository = law_repository
        self.embedding_service = embedding_service
        self.settings = get_settings()
        self.last_trace: dict[str, Any] = {}

    def retrieve(self, query_payload: dict[str, Any], limit: int = 30) -> list[dict[str, Any]]:
        legal_query = str(query_payload.get("legal_query") or query_payload.get("plain_problem") or "").strip()
        keywords = query_payload.get("keywords") if isinstance(query_payload.get("keywords"), list) else []
        raw_expected_acts = query_payload.get("expected_acts") if isinstance(query_payload.get("expected_acts"), list) else []
        expected_acts = raw_expected_acts
        expected_act_types = []
        raw_expected_act_types = []
        if isinstance(query_payload.get("expected_act_types"), list):
            expected_act_types = query_payload.get("expected_act_types") or []
            raw_expected_act_types = list(expected_act_types)
        elif isinstance(query_payload.get("act_type"), str) and query_payload.get("act_type"):
            expected_act_types = [str(query_payload["act_type"])]
            raw_expected_act_types = list(expected_act_types)
        expected_acts = self._resolve_expected_acts(expected_acts)
        expected_act_types = self._resolve_expected_act_types(expected_act_types)
        query_embedding: list[float] | None = None
        query_text = " ".join([legal_query] + [str(item) for item in keywords]).strip()
        embedding_used = False
        if query_text:
            try:
                query_embedding = self.embedding_service.embed_text(query_text)
                embedding_used = query_embedding is not None
            except EmbeddingUnavailableError:
                query_embedding = None
        raw_keyword_candidates = self.law_repository.keyword_search_candidates(
            query=legal_query,
            keywords=keywords,
            expected_acts=[],
            expected_act_types=[],
            limit=limit,
        )
        raw_vector_candidates = self.law_repository.vector_search_candidates(
            query_embedding=query_embedding,
            expected_acts=[],
            expected_act_types=[],
            limit=limit,
        )
        raw_after_act_filter = self._filter_candidates(raw_keyword_candidates + raw_vector_candidates, expected_acts=expected_acts)
        raw_after_act_type_filter = self._filter_candidates(raw_after_act_filter, expected_act_types=expected_act_types)
        raw_after_threshold = self._threshold_candidates(raw_after_act_type_filter)

        attempts: list[dict[str, Any]] = []
        attempts.append(
            self._run_attempt(
                name="main_strict",
                query=legal_query,
                keywords=keywords,
                query_embedding=query_embedding,
                expected_acts=expected_acts,
                expected_act_types=expected_act_types,
                limit=limit,
                penalty=0.0,
            )
        )
        if not attempts[-1]["results"] and expected_act_types:
            attempts.append(
                self._run_attempt(
                    name="main_relaxed_act_types",
                    query=legal_query,
                    keywords=keywords,
                    query_embedding=query_embedding,
                    expected_acts=expected_acts,
                    expected_act_types=[],
                    limit=limit,
                    penalty=0.03,
                )
            )
        if not attempts[-1]["results"] and expected_acts:
            attempts.append(
                self._run_attempt(
                    name="main_relaxed_acts",
                    query=legal_query,
                    keywords=keywords,
                    query_embedding=query_embedding,
                    expected_acts=[],
                    expected_act_types=expected_act_types,
                    limit=limit,
                    penalty=0.05,
                )
            )
        if not self._has_results(attempts) and (expected_acts or expected_act_types):
            attempts.append(
                self._run_attempt(
                    name="main_relaxed_all",
                    query=legal_query,
                    keywords=keywords,
                    query_embedding=query_embedding,
                    expected_acts=[],
                    expected_act_types=[],
                    limit=limit,
                    penalty=0.08,
                )
            )

        for index, item in enumerate(self._query_variants(query_payload), start=1):
            if self._has_results(attempts):
                break
            variant_query = str(item.get("query") or "").strip()
            variant_keywords = item.get("keywords") if isinstance(item.get("keywords"), list) else keywords
            if not variant_query:
                continue
            variant_embedding = None
            variant_text = " ".join([variant_query] + [str(value) for value in variant_keywords]).strip()
            if variant_text:
                try:
                    variant_embedding = self.embedding_service.embed_text(variant_text)
                except EmbeddingUnavailableError:
                    variant_embedding = None
            attempts.append(
                self._run_attempt(
                    name=f"variant_{index}_strict",
                    query=variant_query,
                    keywords=variant_keywords,
                    query_embedding=variant_embedding,
                    expected_acts=expected_acts,
                    expected_act_types=expected_act_types,
                    limit=limit,
                    penalty=0.08,
                )
            )
            if not attempts[-1]["results"] and expected_act_types:
                attempts.append(
                    self._run_attempt(
                        name=f"variant_{index}_relaxed_act_types",
                        query=variant_query,
                        keywords=variant_keywords,
                        query_embedding=variant_embedding,
                        expected_acts=expected_acts,
                        expected_act_types=[],
                        limit=limit,
                        penalty=0.1,
                    )
                )
            if not attempts[-1]["results"] and expected_acts:
                attempts.append(
                    self._run_attempt(
                        name=f"variant_{index}_relaxed_acts",
                        query=variant_query,
                        keywords=variant_keywords,
                        query_embedding=variant_embedding,
                        expected_acts=[],
                        expected_act_types=expected_act_types,
                        limit=limit,
                        penalty=0.12,
                    )
                )
            if not self._has_results(attempts[-3:]) and (expected_acts or expected_act_types):
                attempts.append(
                    self._run_attempt(
                        name=f"variant_{index}_relaxed_all",
                        query=variant_query,
                        keywords=variant_keywords,
                        query_embedding=variant_embedding,
                        expected_acts=[],
                        expected_act_types=[],
                        limit=limit,
                        penalty=0.15,
                    )
                )

        merged = self._merge_attempts(attempts, expected_acts)
        merged_values = self._apply_consumer_service_boosts(list(merged.values()), query_payload)
        result = sorted(merged_values, key=self._candidate_sort_key, reverse=True)[:limit]
        dropped_by_reason = {
            "no_raw_keyword_candidates": 1 if not raw_keyword_candidates else 0,
            "no_raw_vector_candidates": 1 if not raw_vector_candidates else 0,
            "dropped_by_act_filter": max(len(raw_keyword_candidates) + len(raw_vector_candidates) - len(raw_after_act_filter), 0),
            "dropped_by_act_type_filter": max(len(raw_after_act_filter) - len(raw_after_act_type_filter), 0),
            "dropped_by_threshold": max(len(raw_after_act_type_filter) - len(raw_after_threshold), 0),
            "strict_filter_eliminated_all": 1 if (expected_acts or expected_act_types) and attempts and not attempts[0]["results"] else 0,
        }
        self.last_trace = {
            "input_query": legal_query,
            "keywords": keywords,
            "top_k": limit,
            "expected_acts_raw": raw_expected_acts,
            "expected_acts_normalized": expected_acts,
            "expected_act_types_raw": raw_expected_act_types,
            "expected_act_types_normalized": expected_act_types,
            "query_embedding_used": embedding_used,
            "legal_rag.retriever.query_input": {
                "legal_query": legal_query,
                "keywords": keywords,
                "expected_acts_used": expected_acts,
                "expected_act_types_used": expected_act_types,
            },
            "raw_keyword_candidates_count": len(raw_keyword_candidates),
            "raw_vector_candidates_count": len(raw_vector_candidates),
            "raw_keyword_candidates": [self._summarize_candidate(item, "keyword") for item in raw_keyword_candidates[:8]],
            "raw_vector_candidates": [self._summarize_candidate(item, "vector") for item in raw_vector_candidates[:8]],
            "after_act_filter_count": len(raw_after_act_filter),
            "after_act_type_filter_count": len(raw_after_act_type_filter),
            "after_threshold_count": len(raw_after_threshold),
            "legal_rag.retriever.raw_candidates": {
                "keyword_count": len(raw_keyword_candidates),
                "vector_count": len(raw_vector_candidates),
                "keyword_top": [self._summarize_candidate(item, "keyword") for item in raw_keyword_candidates[:5]],
                "vector_top": [self._summarize_candidate(item, "vector") for item in raw_vector_candidates[:5]],
            },
            "legal_rag.retriever.after_act_filter": {
                "count": len(raw_after_act_filter),
                "top": [self._summarize_candidate(item, self._candidate_source(item)) for item in raw_after_act_filter[:5]],
            },
            "legal_rag.retriever.after_act_type_filter": {
                "count": len(raw_after_act_type_filter),
                "top": [self._summarize_candidate(item, self._candidate_source(item)) for item in raw_after_act_type_filter[:5]],
            },
            "legal_rag.retriever.after_threshold": {
                "count": len(raw_after_threshold),
                "top": [self._summarize_candidate(item, self._candidate_source(item)) for item in raw_after_threshold[:5]],
            },
            "legal_rag.retriever.relaxed_retry": [attempt for attempt in attempts if "relaxed" in str(attempt.get("name") or "")],
            "legal_rag.retriever.dropped_by_reason": dropped_by_reason,
            "legal_rag.retrieval.candidates_found": {
                "raw_keyword_count": len(raw_keyword_candidates),
                "raw_vector_count": len(raw_vector_candidates),
                "merged_candidates_count": len(merged_values),
                "returned_candidates_count": len(result),
                "top_candidates": [self._summarize_candidate(item, self._candidate_source(item)) for item in result[:10]],
            },
            "legal_rag.retriever.final_candidates": [self._summarize_candidate(item, self._candidate_source(item)) for item in result[:8]],
            "dropped_by_reason": dropped_by_reason,
            "attempts": attempts,
            "merged_candidates": [self._summarize_candidate(item, self._candidate_source(item)) for item in result],
        }
        return result

    @classmethod
    def normalize_act_name(cls, act_name: str) -> str:
        normalized = " ".join(str(act_name).split()).strip().casefold()
        return cls.ACT_NAME_ALIASES.get(normalized, str(act_name).strip())

    def _resolve_expected_acts(self, expected_acts: list[str]) -> list[str]:
        if not expected_acts:
            return []

        available_acts = set(self.law_repository.get_active_act_names())
        resolved: list[str] = []
        for act_name in expected_acts:
            normalized = self.normalize_act_name(act_name)
            if normalized in available_acts:
                resolved.append(normalized)
            elif act_name in available_acts:
                resolved.append(act_name)

        seen: set[str] = set()
        deduped: list[str] = []
        for act_name in resolved:
            if act_name in seen:
                continue
            seen.add(act_name)
            deduped.append(act_name)
        return deduped

    @classmethod
    def normalize_act_type(cls, act_type: str) -> str:
        normalized = " ".join(str(act_type).split()).strip().casefold()
        return cls.ACT_TYPE_ALIASES.get(normalized, str(act_type).strip())

    def _resolve_expected_act_types(self, expected_act_types: list[str]) -> list[str]:
        if not expected_act_types:
            return []

        available_types = set(self.law_repository.get_active_act_types())
        seen: set[str] = set()
        deduped: list[str] = []
        for act_type in expected_act_types:
            normalized = self.normalize_act_type(act_type)
            if normalized not in available_types:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _run_attempt(
        self,
        name: str,
        query: str,
        keywords: list[str],
        query_embedding: list[float] | None,
        expected_acts: list[str],
        expected_act_types: list[str],
        limit: int,
        penalty: float,
    ) -> dict[str, Any]:
        keyword_candidates = self.law_repository.keyword_search_candidates(
            query=query,
            keywords=keywords,
            expected_acts=[],
            expected_act_types=[],
            limit=limit,
        )
        vector_candidates = self.law_repository.vector_search_candidates(
            query_embedding=query_embedding,
            expected_acts=[],
            expected_act_types=[],
            limit=limit,
        )
        filtered = self._filter_candidates(keyword_candidates + vector_candidates, expected_acts=expected_acts, expected_act_types=expected_act_types)
        thresholded = self._threshold_candidates(filtered)
        merged = self._merge_candidates(thresholded, expected_acts=expected_acts, penalty=penalty, attempt_name=name)
        return {
            "name": name,
            "query": query,
            "keywords": keywords,
            "expected_acts": expected_acts,
            "expected_act_types": expected_act_types,
            "penalty": penalty,
            "raw_keyword_count": len(keyword_candidates),
            "raw_vector_count": len(vector_candidates),
            "filtered_count": len(filtered),
            "threshold_count": len(thresholded),
            "full_results": list(merged.values()),
            "results": [self._summarize_candidate(item, self._candidate_source(item)) for item in merged.values()],
        }

    @staticmethod
    def _query_variants(query_payload: dict[str, Any]) -> list[dict[str, Any]]:
        variants = query_payload.get("queries") if isinstance(query_payload.get("queries"), list) else []
        fallback_keywords = query_payload.get("keywords") if isinstance(query_payload.get("keywords"), list) else []
        combined_fallback = " ".join(
            item for item in ["договор оказания услуг", "неисполнение обязательства", "возврат оплаты", "возмещение убытков", "просрочка исполнения"] if item
        )
        extra = {"query": combined_fallback, "keywords": fallback_keywords}
        result = [item for item in variants if isinstance(item, dict)]
        result.append(extra)
        return result

    @classmethod
    def _filter_candidates(
        cls,
        candidates: list[dict[str, Any]],
        expected_acts: list[str] | None = None,
        expected_act_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        act_set = set(expected_acts or [])
        act_type_set = set(expected_act_types or [])
        for candidate in candidates:
            if act_set and cls.normalize_act_name(str(candidate.get("act_name") or "")) not in act_set:
                continue
            if act_type_set and cls.normalize_act_type(str(candidate.get("act_type") or "")) not in act_type_set:
                continue
            filtered.append(candidate)
        return filtered

    @staticmethod
    def _threshold_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for candidate in candidates:
            keyword_score = float(candidate.get("keyword_score") or 0.0)
            vector_score = float(candidate.get("vector_score") or 0.0)
            if keyword_score <= 0.0 and vector_score <= 0.0:
                continue
            result.append(candidate)
        return result

    def _merge_candidates(
        self,
        candidates: list[dict[str, Any]],
        expected_acts: list[str],
        penalty: float,
        attempt_name: str,
    ) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            article_id = str(candidate["id"])
            item = merged.setdefault(article_id, dict(candidate))
            item["keyword_score"] = max(float(item.get("keyword_score") or 0.0), float(candidate.get("keyword_score") or 0.0))
            item["vector_score"] = max(float(item.get("vector_score") or 0.0), float(candidate.get("vector_score") or 0.0))
            item["retrieval_attempt"] = attempt_name
            item["retrieval_penalty"] = penalty
        for item in merged.values():
            act_match_score = 1.0 if expected_acts and self.normalize_act_name(str(item.get("act_name") or "")) in expected_acts else 0.0
            item["act_match_score"] = act_match_score
            item["combined_score"] = (
                0.45 * float(item.get("keyword_score") or 0.0)
                + 0.45 * float(item.get("vector_score") or 0.0)
                + 0.10 * act_match_score
                - penalty
            )
        return merged

    def _merge_attempts(self, attempts: list[dict[str, Any]], expected_acts: list[str]) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for attempt in attempts:
            for candidate in attempt.get("full_results", []):
                article_id = str(candidate.get("id") or "")
                if not article_id:
                    continue
                current = merged.get(article_id)
                if current is None or float(candidate.get("combined_score") or 0.0) > float(current.get("combined_score") or 0.0):
                    raw = dict(candidate)
                    raw["act_match_score"] = 1.0 if expected_acts and self.normalize_act_name(str(candidate.get("act_name") or "")) in expected_acts else 0.0
                    merged[article_id] = raw
        return merged

    def _apply_consumer_service_boosts(
        self,
        candidates: list[dict[str, Any]],
        query_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not self._is_consumer_service_scenario(query_payload):
            return candidates
        boosted: list[dict[str, Any]] = []
        for candidate in candidates:
            item = dict(candidate)
            text = " ".join(
                [
                    str(item.get("act_name") or ""),
                    str(item.get("article_title") or ""),
                    str(item.get("snippet") or ""),
                    str(item.get("article_text") or ""),
                ]
            ).lower()
            negative_domain = any(
                token in text
                for token in (
                    "ипотек",
                    "залог",
                    "поручитель",
                    "земельн",
                    "наслед",
                    "семейн",
                    "трудов",
                    "корпоратив",
                    "банковск",
                    "кредит",
                    "преддоговор",
                )
            )
            boost = 0.0
            reasons: list[str] = []
            if negative_domain:
                item["retrieval_boost"] = 0.0
                item["boost_strength"] = 0.0
                boosted.append(item)
                continue
            if any(token in text for token in ("услуг", "оказан", "исполнитель", "заказчик", "срок оказания")):
                boost += 0.18
                reasons.append("consumer_service_guard")
            if any(token in text for token in ("потребител", "защите прав потребителей", "зозпп")):
                boost += 0.22
                reasons.append("consumer_act_boost")
            if any(token in text for token in ("возврат", "уплачен", "отказ от договора", "возмещение убытков")):
                boost += 0.08
                reasons.append("refund_or_damages_boost")
            item["retrieval_boost"] = boost
            item["boost_strength"] = boost
            if reasons:
                item["boost_reason"] = ", ".join(reasons)
            item["combined_score"] = float(item.get("combined_score") or 0.0) + boost
            boosted.append(item)
        return boosted

    @staticmethod
    def _is_consumer_service_scenario(query_payload: dict[str, Any]) -> bool:
        detected_claims = {
            str(item).lower() for item in (query_payload.get("detected_claims") if isinstance(query_payload.get("detected_claims"), list) else [])
        }
        secondary_areas = {
            str(item).lower() for item in (query_payload.get("secondary_areas") if isinstance(query_payload.get("secondary_areas"), list) else [])
        }
        text = " ".join(
            [
                str(query_payload.get("plain_problem") or ""),
                str(query_payload.get("legal_query") or ""),
                " ".join(str(item) for item in (query_payload.get("keywords") if isinstance(query_payload.get("keywords"), list) else [])),
            ]
        ).lower()
        return (
            "refund_principal" in detected_claims
            and ("consumer" in secondary_areas or "потребител" in text or "услуг" in text)
            and any(token in text for token in ("услуг", "оказан", "исполнитель"))
        )

    @staticmethod
    def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[float, float]:
        return (
            float(candidate.get("combined_score") or 0.0),
            float(candidate.get("vector_score") or candidate.get("keyword_score") or 0.0),
        )

    @staticmethod
    def _has_results(attempts: list[dict[str, Any]]) -> bool:
        return any(attempt.get("results") for attempt in attempts)

    @staticmethod
    def _candidate_source(candidate: dict[str, Any]) -> str:
        has_keyword = float(candidate.get("keyword_score") or 0.0) > 0.0
        has_vector = float(candidate.get("vector_score") or 0.0) > 0.0
        if has_keyword and has_vector:
            return "both"
        if has_keyword:
            return "keyword"
        if has_vector:
            return "vector"
        return "unknown"

    @staticmethod
    def _summarize_candidate(candidate: dict[str, Any], source: str) -> dict[str, Any]:
        snippet = " ".join(str(candidate.get("article_text") or "").split()).strip()[:180]
        return {
            "id": str(candidate.get("id") or ""),
            "act_name": candidate.get("act_name"),
            "article_number": candidate.get("article_number"),
            "title": candidate.get("article_title"),
            "score": float(candidate.get("combined_score") or candidate.get("keyword_score") or candidate.get("vector_score") or 0.0),
            "keyword_score": float(candidate.get("keyword_score") or 0.0),
            "vector_score": float(candidate.get("vector_score") or 0.0),
            "source": source,
            "snippet": snippet,
        }
