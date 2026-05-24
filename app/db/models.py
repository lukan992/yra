import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.vector_type import VectorType


settings = get_settings()


class Base(DeclarativeBase):
    pass


class ClaimRequest(Base):
    __tablename__ = "claim_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    runs: Mapped[list["PipelineRun"]] = relationship(back_populates="request")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("claim_requests.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    case_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    request: Mapped[ClaimRequest] = relationship(back_populates="runs")
    steps: Mapped[list["PipelineStep"]] = relationship(back_populates="run")


class PipelineStep(Base):
    __tablename__ = "pipeline_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("pipeline_runs.id"), nullable=False)
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    input_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_json: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    error_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped[PipelineRun] = relationship(back_populates="steps")


class LawArticle(Base):
    __tablename__ = "law_articles"
    __table_args__ = (
        UniqueConstraint("act_name", "article_number", name="uq_law_articles_act_name_article_number"),
        Index("ix_law_articles_is_active", "is_active"),
        Index("ix_law_articles_act_name", "act_name"),
        Index("ix_law_articles_act_type", "act_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    act_name: Mapped[str] = mapped_column(String(255), nullable=False)
    act_type: Mapped[str] = mapped_column(String(64), nullable=False)
    section_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    section_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    subsection_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subsection_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    chapter_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chapter_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    article_number: Mapped[str] = mapped_column(String(64), nullable=False)
    article_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    article_text: Mapped[str] = mapped_column(Text, nullable=False)
    article_parts: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    source_file: Mapped[str] = mapped_column(String(512), nullable=False)
    article_status: Mapped[str] = mapped_column(String(32), default="unknown", server_default="unknown", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    search_vector: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(VectorType(settings.embedding_dim), nullable=True)
    legal_area: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tags: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    situations: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    consequences: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    deadlines: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    exceptions: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    related_articles: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    edition_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LawArticleReference(Base):
    __tablename__ = "law_article_references"
    __table_args__ = (
        Index("ix_law_article_references_source_article_id", "source_article_id"),
        Index("ix_law_article_references_target_article_id", "target_article_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_article_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("law_articles.id"), nullable=False)
    target_act_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_article_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_article_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("law_articles.id"), nullable=True)
    relation_type: Mapped[str] = mapped_column(
        String(64), default="explicit_reference", server_default="explicit_reference", nullable=False
    )
    source_fragment: Mapped[str | None] = mapped_column(Text, nullable=True)


class GeneratedClaim(Base):
    __tablename__ = "generated_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("claim_requests.id"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("pipeline_runs.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    validation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    used_laws_json: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
