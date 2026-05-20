import re
from typing import Any

from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from app.db.models import LawArticle


class LawRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def has_active_articles(self) -> bool:
        return self.db.query(LawArticle.id).filter(LawArticle.is_active.is_(True)).first() is not None

    def search(self, query: str, tags: list[str] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        clean_query = (query or "").strip()
        search_terms = self._build_search_terms(clean_query, tags)
        if not search_terms:
            return []

        filters = []
        for term in search_terms:
            like_term = f"%{term}%"
            filters.append(LawArticle.article_text.ilike(like_term))
            filters.append(LawArticle.article_title.ilike(like_term))
            filters.append(cast(LawArticle.tags, String).ilike(like_term))

        text_vector = func.to_tsvector(
            "russian",
            func.concat_ws(" ", LawArticle.article_title, LawArticle.article_text, cast(LawArticle.tags, String)),
        )
        ts_query_text = self._build_ts_query(clean_query, search_terms)
        web_query = func.websearch_to_tsquery("russian", ts_query_text)
        rank = func.ts_rank(text_vector, web_query)

        rows = (
            self.db.query(LawArticle)
            .filter(LawArticle.is_active.is_(True))
            .filter(or_(text_vector.op("@@")(web_query), or_(*filters)))
            .order_by(rank.desc())
            .limit(top_k)
            .all()
        )
        return [self._to_dict(row) for row in rows]

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

    @staticmethod
    def _to_dict(article: LawArticle) -> dict[str, Any]:
        return {
            "id": str(article.id),
            "law_name": article.law_name,
            "article_number": article.article_number,
            "article_title": article.article_title,
            "article_text": article.article_text,
            "tags": article.tags,
        }
