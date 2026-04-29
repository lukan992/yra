from typing import Any

from pydantic import BaseModel


class LawArticleSchema(BaseModel):
    id: str
    law_name: str
    article_number: str
    article_title: str | None
    article_text: str
    tags: Any | None = None


class LawSearchQuery(BaseModel):
    query: str
    tags: list[str] = []
