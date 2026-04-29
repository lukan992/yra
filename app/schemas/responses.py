from typing import Any, Literal

from pydantic import BaseModel, Field


PipelineStatus = Literal["claim_generated", "need_more_info", "route_to_lawyer", "error"]


class ErrorResponse(BaseModel):
    code: str
    message: str


class ClaimAnalyzeResponse(BaseModel):
    status: PipelineStatus
    request_id: str
    run_id: str
    case_type: str | None = None
    summary: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[Any] = Field(default_factory=list)
    clarifying_questions: list[Any] = Field(default_factory=list)
    used_laws: list[Any] = Field(default_factory=list)
    claim_json: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    error: ErrorResponse | None = None
