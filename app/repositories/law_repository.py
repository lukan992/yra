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
        search_terms = [clean_query] + [tag for tag in (tags or []) if tag]
        search_terms = [term for term in search_terms if term]
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
        web_query = func.websearch_to_tsquery("russian", clean_query)
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
    def _to_dict(article: LawArticle) -> dict[str, Any]:
        return {
            "id": str(article.id),
            "law_name": article.law_name,
            "article_number": article.article_number,
            "article_title": article.article_title,
            "article_text": article.article_text,
            "tags": article.tags,
        }
