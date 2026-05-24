from typing import Any

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
    }

    def __init__(self, law_repository: LawRepository, embedding_service: EmbeddingService) -> None:
        self.law_repository = law_repository
        self.embedding_service = embedding_service
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
        keyword_candidates = self.law_repository.keyword_search_candidates(
            query=legal_query,
            keywords=keywords,
            expected_acts=expected_acts,
            expected_act_types=expected_act_types,
            limit=limit,
        )
        vector_candidates = self.law_repository.vector_search_candidates(
            query_embedding=query_embedding,
            expected_acts=expected_acts,
            expected_act_types=expected_act_types,
            limit=limit,
        )

        merged: dict[str, dict[str, Any]] = {}
        for candidate in keyword_candidates + vector_candidates:
            article_id = str(candidate["id"])
            item = merged.setdefault(article_id, dict(candidate))
            item["keyword_score"] = max(float(item.get("keyword_score") or 0.0), float(candidate.get("keyword_score") or 0.0))
            item["vector_score"] = max(float(item.get("vector_score") or 0.0), float(candidate.get("vector_score") or 0.0))

        for item in merged.values():
            act_match_score = 1.0 if expected_acts and item.get("act_name") in expected_acts else 0.0
            item["act_match_score"] = act_match_score
            item["combined_score"] = (
                0.45 * float(item.get("keyword_score") or 0.0)
                + 0.45 * float(item.get("vector_score") or 0.0)
                + 0.10 * act_match_score
            )
        result = sorted(merged.values(), key=lambda row: row["combined_score"], reverse=True)[:limit]
        self.last_trace = {
            "input_query": legal_query,
            "keywords": keywords,
            "top_k": limit,
            "expected_acts_raw": raw_expected_acts,
            "expected_acts_normalized": expected_acts,
            "expected_act_types_raw": raw_expected_act_types,
            "expected_act_types_normalized": expected_act_types,
            "query_embedding_used": embedding_used,
            "keyword_candidates": [self._summarize_candidate(item, "keyword") for item in keyword_candidates],
            "vector_candidates": [self._summarize_candidate(item, "vector") for item in vector_candidates],
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

        seen: set[str] = set()
        deduped: list[str] = []
        for act_type in expected_act_types:
            normalized = self.normalize_act_type(act_type)
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

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
