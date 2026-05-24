from typing import Any

from pydantic import BaseModel


class LawArticleSchema(BaseModel):
    id: str
    act_name: str
    act_type: str | None = None
    article_number: str
    article_title: str | None
    article_text: str
    source_file: str | None = None
    tags: Any | None = None


class LawSearchQuery(BaseModel):
    query: str
    tags: list[str] = []
