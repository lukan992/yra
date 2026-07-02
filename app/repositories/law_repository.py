import re
from typing import Any

from sqlalchemy import delete, func, or_, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import LawArticle, LawArticleReference


class LawRepository:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def has_active_articles(self) -> bool:
        return self.db.query(LawArticle.id).filter(LawArticle.is_active.is_(True)).first() is not None

    def get_active_act_names(self) -> list[str]:
        rows = self.db.query(LawArticle.act_name).filter(LawArticle.is_active.is_(True)).distinct().all()
        return [str(row[0]) for row in rows if row and row[0]]

    def get_active_act_types(self) -> list[str]:
        rows = self.db.query(LawArticle.act_type).filter(LawArticle.is_active.is_(True)).distinct().all()
        return [str(row[0]) for row in rows if row and row[0]]

    def keyword_search_candidates(
        self,
        query: str,
        keywords: list[str] | None = None,
        expected_acts: list[str] | None = None,
        expected_act_types: list[str] | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        clean_query = (query or "").strip()
        search_terms = self._build_search_terms(clean_query, keywords)
        if not search_terms:
            return []

        filters = []
        for term in search_terms:
            like_term = f"%{term}%"
            filters.extend(
                [
                    LawArticle.search_vector.ilike(like_term),
                    LawArticle.article_text.ilike(like_term),
                    LawArticle.article_title.ilike(like_term),
                    LawArticle.chapter_title.ilike(like_term),
                    LawArticle.section_title.ilike(like_term),
                ]
            )

        text_vector = func.to_tsvector("russian", func.coalesce(LawArticle.search_vector, ""))
        ts_query_text = self._build_ts_query(clean_query, search_terms)
        web_query = func.websearch_to_tsquery("russian", ts_query_text)
        rank = func.ts_rank(text_vector, web_query)

        query_builder = self.db.query(LawArticle, rank.label("keyword_score")).filter(LawArticle.is_active.is_(True))
        if expected_acts:
            query_builder = query_builder.filter(LawArticle.act_name.in_(expected_acts))
        if expected_act_types:
            query_builder = query_builder.filter(LawArticle.act_type.in_(expected_act_types))

        rows = (
            query_builder.filter(or_(text_vector.op("@@")(web_query), or_(*filters)))
            .order_by(rank.desc())
            .limit(limit)
            .all()
        )
        return [self._to_candidate(article, keyword_score=float(score or 0.0)) for article, score in rows]

    def vector_search_candidates(
        self,
        query_embedding: list[float] | None,
        expected_acts: list[str] | None = None,
        expected_act_types: list[str] | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        if not query_embedding:
            return []
        if len(query_embedding) != self.settings.embedding_dim:
            return []
        query_embedding_literal = "[" + ",".join(str(float(item)) for item in query_embedding) + "]"
        filters = ["is_active = true", "embedding IS NOT NULL"]
        params: dict[str, Any] = {"query_embedding": query_embedding_literal, "limit": limit}
        if expected_acts:
            filters.append("act_name = ANY(:expected_acts)")
            params["expected_acts"] = expected_acts
        if expected_act_types:
            filters.append("act_type = ANY(:expected_act_types)")
            params["expected_act_types"] = expected_act_types

        sql = text(
            f"""
            SELECT
                CAST(id AS text) AS id,
                act_name,
                act_type,
                act_name AS law_name,
                section_number,
                section_title,
                subsection_number,
                subsection_title,
                chapter_number,
                chapter_title,
                article_number,
                article_title,
                article_text,
                article_parts,
                source_file,
                article_status,
                is_active,
                content_hash,
                legal_area,
                tags,
                0.0 AS keyword_score,
                1 - (embedding <=> CAST(:query_embedding AS vector)) AS vector_score
            FROM law_articles
            WHERE {' AND '.join(filters)}
            ORDER BY vector_score DESC
            LIMIT :limit
            """
        )
        rows = self.db.execute(sql, params).mappings().all()
        return [dict(row) for row in rows]

    def search(self, query: str, tags: list[str] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        return [self._to_public_dict(item) for item in self.keyword_search_candidates(query, tags, limit=top_k)]

    def upsert_article(self, payload: dict[str, Any]) -> tuple[LawArticle, bool]:
        article = (
            self.db.query(LawArticle)
            .filter(LawArticle.act_name == payload["act_name"])
            .filter(LawArticle.article_number == payload["article_number"])
            .one_or_none()
        )
        created = article is None
        if article is None:
            article = LawArticle(**payload)
            self.db.add(article)
        else:
            for key, value in payload.items():
                setattr(article, key, value)
        self.db.flush()
        return article, created

    def get_articles_for_embedding(self, limit: int = 100) -> list[LawArticle]:
        rows = self.db.query(LawArticle).filter(LawArticle.is_active.is_(True)).order_by(LawArticle.created_at.asc()).all()
        result: list[LawArticle] = []
        for article in rows:
            embedding = article.embedding
            if embedding is None or len(embedding) != self.settings.embedding_dim:
                result.append(article)
            if len(result) >= limit:
                break
        return result

    def get_active_article(self, act_name: str, article_number: str) -> dict[str, Any] | None:
        row = (
            self.db.query(LawArticle)
            .filter(LawArticle.act_name == act_name)
            .filter(LawArticle.article_number == article_number)
            .filter(LawArticle.is_active.is_(True))
            .one_or_none()
        )
        return self._to_public_dict(self._to_candidate(row)) if row else None

    def replace_references(self, source_article_id: str, references: list[dict[str, Any]]) -> None:
        self.db.execute(delete(LawArticleReference).where(LawArticleReference.source_article_id == source_article_id))
        for item in references:
            target_id = (
                self.db.query(LawArticle.id)
                .filter(LawArticle.act_name == item.get("target_act_name"))
                .filter(LawArticle.article_number == item.get("target_article_number"))
                .scalar()
            )
            self.db.add(
                LawArticleReference(
                    source_article_id=source_article_id,
                    target_act_name=item.get("target_act_name"),
                    target_article_number=item.get("target_article_number"),
                    target_article_id=target_id,
                    relation_type=item.get("relation_type") or "explicit_reference",
                    source_fragment=item.get("source_fragment"),
                )
            )
        self.db.flush()

    @staticmethod
    def build_search_vector(payload: dict[str, Any]) -> str:
        fields = [
            payload.get("act_name"),
            payload.get("act_type"),
            payload.get("section_title"),
            payload.get("subsection_title"),
            payload.get("chapter_title"),
            payload.get("article_title"),
            payload.get("article_text"),
        ]
        return " ".join(str(field).strip() for field in fields if field)

    @staticmethod
    def _build_search_terms(query: str, tags: list[str] | None = None) -> list[str]:
        raw_terms = [query] + [tag for tag in (tags or []) if tag]
        words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]{3,}", query.lower())
        raw_terms.extend(words)

        seen: set[str] = set()
        result: list[str] = []
        for term in raw_terms:
            normalized = " ".join(str(term).split()).strip()
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(normalized)
        return result

    @staticmethod
    def _build_ts_query(query: str, search_terms: list[str]) -> str:
        words = [term for term in search_terms if " " not in term]
        if words:
            return " OR ".join(words)
        return query

    @classmethod
    def _to_candidate(cls, article: LawArticle, keyword_score: float = 0.0, vector_score: float = 0.0) -> dict[str, Any]:
        return {
            "id": str(article.id),
            "act_name": article.act_name,
            "act_type": article.act_type,
            "law_name": article.act_name,
            "section_number": article.section_number,
            "section_title": article.section_title,
            "subsection_number": article.subsection_number,
            "subsection_title": article.subsection_title,
            "chapter_number": article.chapter_number,
            "chapter_title": article.chapter_title,
            "article_number": article.article_number,
            "article_title": article.article_title,
            "article_text": article.article_text,
            "article_parts": article.article_parts,
            "source_file": article.source_file,
            "article_status": article.article_status,
            "is_active": article.is_active,
            "content_hash": article.content_hash,
            "legal_area": article.legal_area,
            "tags": article.tags,
            "keyword_score": keyword_score,
            "vector_score": vector_score,
        }

    @staticmethod
    def _to_public_dict(candidate: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in candidate.items() if key not in {"keyword_score", "vector_score"}}
