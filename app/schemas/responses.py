from typing import Any, Literal

from pydantic import BaseModel, Field


PipelineStatus = Literal["claim_generated", "need_more_info", "route_to_lawyer", "legal_guidance", "error"]


class ErrorResponse(BaseModel):
    code: str
    message: str


class UsedLaw(BaseModel):
    id: str
    act_name: str
    act_type: str | None = None
    section_number: str | None = None
    section_title: str | None = None
    chapter_number: str | None = None
    chapter_title: str | None = None
    article_number: str
    article_title: str | None = None
    article_text: str | None = None
    relevance_score: float | None = None
    applicability: str | None = None
    why_relevant: str | None = None
    regulates: str | None = None
    relation_type: str | None = None
    source_file: str | None = None


class ClaimAnalyzeResponse(BaseModel):
    status: PipelineStatus
    request_id: str
    run_id: str
    case_type: str | None = None
    summary: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[Any] = Field(default_factory=list)
    clarifying_questions: list[Any] = Field(default_factory=list)
    used_laws: list[UsedLaw] = Field(default_factory=list)
    legal_area: dict[str, Any] | None = None
    legal_context_confidence: float | None = None
    legal_context_warnings: list[str] = Field(default_factory=list)
    guidance: dict[str, Any] | None = None
    claim_json: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    error: ErrorResponse | None = None
