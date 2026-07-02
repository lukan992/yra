from typing import Any

from pydantic import BaseModel, Field, field_validator


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        normalized = _normalize_text(item)
        if normalized:
            result.append(normalized)
    return result


class ArticleSemanticEffectSchema(BaseModel):
    effect_type: str = "other"
    effect_scope: str = "supporting"
    effect_description: str = ""
    trigger_conditions: list[str] = Field(default_factory=list)
    evidence_quote: str = ""
    limitations: list[str] = Field(default_factory=list)
    confidence: float = 0.0

    @field_validator("effect_type", "effect_scope", "effect_description", "evidence_quote", mode="before")
    @classmethod
    def _normalize_fields(cls, value: Any) -> str:
        return _normalize_text(value)

    @field_validator("trigger_conditions", "limitations", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: Any) -> list[str]:
        return _normalize_list(value)


class ArticleNotCoveredEffectSchema(BaseModel):
    effect_type: str = "other"
    reason: str = ""

    @field_validator("effect_type", "reason", mode="before")
    @classmethod
    def _normalize_fields(cls, value: Any) -> str:
        return _normalize_text(value)


class ArticleSemanticAnalysisSchema(BaseModel):
    article_number: str = ""
    article_title: str = ""
    main_institute: str = ""
    summary: str = ""
    legal_effects: list[ArticleSemanticEffectSchema] = Field(default_factory=list)
    not_covered_effects: list[ArticleNotCoveredEffectSchema] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("article_number", "article_title", "main_institute", "summary", mode="before")
    @classmethod
    def _normalize_fields(cls, value: Any) -> str:
        return _normalize_text(value)

    @field_validator("warnings", mode="before")
    @classmethod
    def _normalize_warnings(cls, value: Any) -> list[str]:
        return _normalize_list(value)


class ClaimMatchSchema(BaseModel):
    claim: str = ""
    matched_effect_type: str | None = None
    matched_effect_scope: str | None = None
    condition_status: str = "not_applicable"
    matched_facts: list[str] = Field(default_factory=list)
    missing_conditions: list[str] = Field(default_factory=list)
    proposed_coverage_type: str = "no_coverage"
    evidence_quote: str | None = None
    reason: str = ""
    confidence: float = 0.0

    @field_validator(
        "claim",
        "matched_effect_type",
        "matched_effect_scope",
        "condition_status",
        "proposed_coverage_type",
        "evidence_quote",
        "reason",
        mode="before",
    )
    @classmethod
    def _normalize_nullable_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        return _normalize_text(value)

    @field_validator("matched_facts", "missing_conditions", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: Any) -> list[str]:
        return _normalize_list(value)


class ClaimMatcherOutputSchema(BaseModel):
    article_number: str = ""
    article_title: str = ""
    claim_matches: list[ClaimMatchSchema] = Field(default_factory=list)
    overall_notes: list[str] = Field(default_factory=list)

    @field_validator("article_number", "article_title", mode="before")
    @classmethod
    def _normalize_fields(cls, value: Any) -> str:
        return _normalize_text(value)

    @field_validator("overall_notes", mode="before")
    @classmethod
    def _normalize_notes(cls, value: Any) -> list[str]:
        return _normalize_list(value)


class CoverageGateEntrySchema(BaseModel):
    claim: str
    article_id: str
    article_number: str = ""
    article_title: str = ""
    effect_type: str = "other"
    effect_scope: str = "supporting"
    coverage_type: str = "no_coverage"
    counts_as_covered: bool = False
    user_visible: bool = False
    trigger_conditions: list[str] = Field(default_factory=list)
    trigger_conditions_satisfied: bool = False
    missing_conditions: list[str] = Field(default_factory=list)
    evidence_quote: str = ""
    effect_description: str = ""
    reason: str = ""

    @field_validator(
        "claim",
        "article_id",
        "article_number",
        "article_title",
        "effect_type",
        "effect_scope",
        "coverage_type",
        "evidence_quote",
        "effect_description",
        "reason",
        mode="before",
    )
    @classmethod
    def _normalize_fields(cls, value: Any) -> str:
        return _normalize_text(value)

    @field_validator("trigger_conditions", "missing_conditions", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: Any) -> list[str]:
        return _normalize_list(value)
