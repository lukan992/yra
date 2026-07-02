from typing import Any

from pydantic import BaseModel, Field, field_validator


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


class LawQueryItem(BaseModel):
    purpose: str = "other"
    query: str = ""
    desired_legal_effect: str | None = None
    keywords: list[str] = Field(default_factory=list)


class LawQueryPayload(BaseModel):
    plain_problem: str = ""
    legal_query: str = ""
    queries: list[LawQueryItem] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    expected_acts: list[str] = Field(default_factory=list)
    expected_act_types: list[str] = Field(default_factory=list)
    detected_claims: list[str] = Field(default_factory=list)
    search_notes: list[str] = Field(default_factory=list)
    fallback_used: bool = False

    @field_validator("plain_problem", "legal_query", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> str:
        return " ".join(str(value or "").split()).strip()

    @field_validator("keywords", "expected_acts", "expected_act_types", "detected_claims", "search_notes", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            normalized = " ".join(str(item or "").split()).strip()
            if normalized:
                result.append(normalized)
        return result
