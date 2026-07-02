import os
from functools import lru_cache

from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="local", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    database_url: str = Field(
        default="postgresql+psycopg://legal_mvp:legal_mvp@postgres:5432/legal_mvp",
        alias="DATABASE_URL",
    )
    postgres_db: str = Field(default="legal_mvp", alias="POSTGRES_DB")
    postgres_user: str = Field(default="legal_mvp", alias="POSTGRES_USER")
    postgres_password: str = Field(default="legal_mvp", alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")

    litellm_base_url: str = Field(default="", alias="LITELLM_BASE_URL")
    litellm_api_key: str = Field(default="", alias="LITELLM_API_KEY")
    litellm_main_model: str = Field(default="openai/qwen3.6-35b", alias="LITELLM_MAIN_MODEL")
    litellm_validator_model: str = Field(default="openai/qwen3.6-35b", alias="LITELLM_VALIDATOR_MODEL")
    litellm_embedding_model: str = Field(default="qwen3-embedding-8b", alias="LITELLM_EMBEDDING_MODEL")
    litellm_temperature: float = Field(default=0.0, alias="LITELLM_TEMPERATURE")
    litellm_timeout_seconds: int = Field(default=120, alias="LITELLM_TIMEOUT_SECONDS")
    litellm_max_retries: int = Field(default=2, alias="LITELLM_MAX_RETRIES")
    litellm_json_response_format_enabled: bool = Field(default=True, alias="LITELLM_JSON_RESPONSE_FORMAT_ENABLED")

    legal_fact_extraction_model: str = Field(default="openai/qwen3.6-35b", alias="LEGAL_FACT_EXTRACTION_MODEL")
    legal_area_classifier_model: str = Field(default="openai/qwen3.6-35b", alias="LEGAL_AREA_CLASSIFIER_MODEL")
    law_query_builder_model: str = Field(default="openai/qwen3.6-35b", alias="LAW_QUERY_BUILDER_MODEL")
    article_semantic_analyzer_model: str = Field(default="openai/qwen3.6-35b", alias="ARTICLE_SEMANTIC_ANALYZER_MODEL")
    claim_matcher_model: str = Field(default="openai/qwen3.6-35b", alias="CLAIM_MATCHER_MODEL")
    claim_entailment_model: str = Field(default="openai/qwen3.6-35b", alias="CLAIM_ENTAILMENT_MODEL")
    guidance_generation_model: str = Field(default="openai/qwen3.6-35b", alias="GUIDANCE_GENERATION_MODEL")
    legal_context_validator_model: str = Field(default="openai/qwen3.6-35b", alias="LEGAL_CONTEXT_VALIDATOR_MODEL")
    legal_rag_embedding_model: str = Field(default="qwen3-embedding-8b", alias="LEGAL_RAG_EMBEDDING_MODEL")

    embedding_provider: str = Field(default="ollama", alias="EMBEDDING_PROVIDER")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    embedding_model: str = Field(default="qwen3-embedding-8b", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(default=4096, alias="EMBEDDING_DIM")
    embedding_timeout_seconds: int = Field(default=120, alias="EMBEDDING_TIMEOUT_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=True, alias="LOG_JSON")
    log_file: str = Field(default="logs/yra.log", alias="LOG_FILE")
    log_rotation: str = Field(default="10 MB", alias="LOG_ROTATION")
    log_retention: str = Field(default="10 days", alias="LOG_RETENTION")
    log_prompts: bool = Field(default=False, alias="LOG_PROMPTS")
    log_rag_trace: bool = Field(default=False, alias="LOG_RAG_TRACE")
    log_rag_trace_full: bool = Field(default=False, alias="LOG_RAG_TRACE_FULL")
    legal_rag_retrieval_top_k: int = Field(default=100, alias="LEGAL_RAG_RETRIEVAL_TOP_K")
    legal_rag_rerank_input_max: int = Field(default=100, alias="LEGAL_RAG_RERANK_INPUT_MAX")
    legal_rag_llm_article_top_k: int = Field(default=12, alias="LEGAL_RAG_LLM_ARTICLE_TOP_K")
    legal_rag_rerank_top_k: int = Field(default=12, alias="LEGAL_RAG_RERANK_TOP_K")
    legal_rag_llm_article_top_k_on_rerank_fail: int = Field(default=5, alias="LEGAL_RAG_LLM_ARTICLE_TOP_K_ON_RERANK_FAIL")
    legal_rag_rerank_backend: str = Field(default="litellm", alias="LEGAL_RAG_RERANK_BACKEND")
    legal_rag_rerank_model: str = Field(default="qwen3-reranker-8b", alias="LEGAL_RAG_RERANK_MODEL")
    legal_rag_rerank_url: str = Field(default="", alias="LEGAL_RAG_RERANK_URL")
    legal_rag_rerank_timeout_ms: int = Field(default=60000, alias="LEGAL_RAG_RERANK_TIMEOUT_MS")
    legal_rag_rerank_timeout_seconds: int = Field(default=60, alias="LEGAL_RAG_RERANK_TIMEOUT_SECONDS")
    legal_rag_rerank_payload_format: str = Field(default="openai_compatible", alias="LEGAL_RAG_RERANK_PAYLOAD_FORMAT")
    article_semantic_analyzer_prompt_version: str = Field(default="v1", alias="ARTICLE_SEMANTIC_ANALYZER_PROMPT_VERSION")
    article_semantic_analyzer_concurrency: int = Field(default=3, alias="ARTICLE_SEMANTIC_ANALYZER_CONCURRENCY")
    article_semantic_analyzer_timeout_ms: int = Field(default=25000, alias="ARTICLE_SEMANTIC_ANALYZER_TIMEOUT_MS")

    @field_validator("app_env", mode="before")
    @classmethod
    def blank_app_env_uses_default(cls, value: Any) -> Any:
        return "local" if value == "" else value

    @field_validator("app_host", mode="before")
    @classmethod
    def blank_app_host_uses_default(cls, value: Any) -> Any:
        return "0.0.0.0" if value == "" else value

    @field_validator("app_port", mode="before")
    @classmethod
    def blank_app_port_uses_default(cls, value: Any) -> Any:
        return 8000 if value == "" else value

    @field_validator("database_url", mode="before")
    @classmethod
    def blank_database_url_uses_default(cls, value: Any) -> Any:
        if value == "":
            return "postgresql+psycopg://legal_mvp:legal_mvp@postgres:5432/legal_mvp"
        return value

    @field_validator("postgres_db", mode="before")
    @classmethod
    def blank_postgres_db_uses_default(cls, value: Any) -> Any:
        return "legal_mvp" if value == "" else value

    @field_validator("postgres_user", mode="before")
    @classmethod
    def blank_postgres_user_uses_default(cls, value: Any) -> Any:
        return "legal_mvp" if value == "" else value

    @field_validator("postgres_password", mode="before")
    @classmethod
    def blank_postgres_password_uses_default(cls, value: Any) -> Any:
        return "legal_mvp" if value == "" else value

    @field_validator("postgres_host", mode="before")
    @classmethod
    def blank_postgres_host_uses_default(cls, value: Any) -> Any:
        return "postgres" if value == "" else value

    @field_validator("postgres_port", mode="before")
    @classmethod
    def blank_postgres_port_uses_default(cls, value: Any) -> Any:
        return 5432 if value == "" else value

    @field_validator("litellm_timeout_seconds", mode="before")
    @classmethod
    def blank_litellm_timeout_uses_default(cls, value: Any) -> Any:
        return 120 if value == "" else value

    @field_validator("litellm_main_model", mode="before")
    @classmethod
    def blank_litellm_main_model_uses_default(cls, value: Any) -> Any:
        return "openai/qwen3.6-35b" if value == "" else value

    @field_validator("litellm_validator_model", mode="before")
    @classmethod
    def blank_litellm_validator_model_uses_default(cls, value: Any) -> Any:
        return "openai/qwen3.6-35b" if value == "" else value

    @field_validator("litellm_embedding_model", mode="before")
    @classmethod
    def blank_litellm_embedding_model_uses_default(cls, value: Any) -> Any:
        return "qwen3-embedding-8b" if value == "" else value

    @field_validator(
        "legal_fact_extraction_model",
        "legal_area_classifier_model",
        "law_query_builder_model",
        "article_semantic_analyzer_model",
        "claim_matcher_model",
        "claim_entailment_model",
        "guidance_generation_model",
        "legal_context_validator_model",
        mode="before",
    )
    @classmethod
    def blank_stage_models_use_default(cls, value: Any) -> Any:
        return "openai/qwen3.6-35b" if value == "" else value

    @field_validator("litellm_temperature", mode="before")
    @classmethod
    def blank_litellm_temperature_uses_default(cls, value: Any) -> Any:
        return 0.0 if value == "" else value

    @field_validator("litellm_max_retries", mode="before")
    @classmethod
    def blank_litellm_retries_uses_default(cls, value: Any) -> Any:
        return 2 if value == "" else value

    @field_validator("embedding_provider", mode="before")
    @classmethod
    def blank_embedding_provider_uses_default(cls, value: Any) -> Any:
        return "ollama" if value == "" else value

    @field_validator("ollama_base_url", mode="before")
    @classmethod
    def blank_ollama_base_url_uses_default(cls, value: Any) -> Any:
        return "http://localhost:11434" if value == "" else value

    @field_validator("embedding_model", mode="before")
    @classmethod
    def blank_embedding_model_uses_default(cls, value: Any) -> Any:
        return "qwen3-embedding-8b" if value == "" else value

    @field_validator("embedding_dim", mode="before")
    @classmethod
    def blank_embedding_dim_uses_default(cls, value: Any) -> Any:
        return 4096 if value == "" else value

    @field_validator("embedding_timeout_seconds", mode="before")
    @classmethod
    def blank_embedding_timeout_uses_default(cls, value: Any) -> Any:
        return 120 if value == "" else value

    @field_validator("log_level", mode="before")
    @classmethod
    def blank_log_level_uses_default(cls, value: Any) -> Any:
        return "INFO" if value == "" else value

    @field_validator("log_file", mode="before")
    @classmethod
    def blank_log_file_uses_default(cls, value: Any) -> Any:
        return "logs/yra.log" if value == "" else value

    @field_validator("log_rotation", mode="before")
    @classmethod
    def blank_log_rotation_uses_default(cls, value: Any) -> Any:
        return "10 MB" if value == "" else value

    @field_validator("log_retention", mode="before")
    @classmethod
    def blank_log_retention_uses_default(cls, value: Any) -> Any:
        return "10 days" if value == "" else value

    @field_validator("legal_rag_rerank_top_k", mode="before")
    @classmethod
    def blank_legal_rag_rerank_top_k_uses_default(cls, value: Any) -> Any:
        return 12 if value == "" else value

    @field_validator("legal_rag_retrieval_top_k", mode="before")
    @classmethod
    def blank_legal_rag_retrieval_top_k_uses_default(cls, value: Any) -> Any:
        return 100 if value == "" else value

    @field_validator("legal_rag_rerank_input_max", mode="before")
    @classmethod
    def blank_legal_rag_rerank_input_max_uses_default(cls, value: Any) -> Any:
        return 100 if value == "" else value

    @field_validator("legal_rag_llm_article_top_k", mode="before")
    @classmethod
    def blank_legal_rag_llm_article_top_k_uses_default(cls, value: Any) -> Any:
        return 12 if value == "" else value

    @field_validator("legal_rag_llm_article_top_k_on_rerank_fail", mode="before")
    @classmethod
    def blank_legal_rag_llm_article_top_k_on_rerank_fail_uses_default(cls, value: Any) -> Any:
        return 5 if value == "" else value

    @field_validator("legal_rag_rerank_backend", mode="before")
    @classmethod
    def blank_legal_rag_rerank_backend_uses_default(cls, value: Any) -> Any:
        return "litellm" if value == "" else value

    @field_validator("legal_rag_rerank_model", mode="before")
    @classmethod
    def blank_legal_rag_rerank_model_uses_default(cls, value: Any) -> Any:
        return "qwen3-reranker-8b" if value == "" else value

    @field_validator("legal_rag_rerank_url", mode="before")
    @classmethod
    def blank_legal_rag_rerank_url_uses_default(cls, value: Any) -> Any:
        return "" if value == "" else value

    @field_validator("legal_rag_rerank_timeout_ms", mode="before")
    @classmethod
    def blank_legal_rag_rerank_timeout_ms_uses_default(cls, value: Any) -> Any:
        return 60000 if value == "" else value

    @field_validator("legal_rag_rerank_timeout_seconds", mode="before")
    @classmethod
    def blank_legal_rag_rerank_timeout_uses_default(cls, value: Any) -> Any:
        return 60 if value == "" else value

    @field_validator("legal_rag_rerank_payload_format", mode="before")
    @classmethod
    def blank_legal_rag_rerank_payload_format_uses_default(cls, value: Any) -> Any:
        return "openai_compatible" if value == "" else value

    @field_validator("legal_rag_rerank_backend", mode="after")
    @classmethod
    def normalize_legal_rag_rerank_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"litellm", "ollama", "custom_http"}:
            raise ValueError("LEGAL_RAG_RERANK_BACKEND must be one of: litellm, ollama, custom_http")
        return normalized

    @field_validator("article_semantic_analyzer_prompt_version", mode="before")
    @classmethod
    def blank_article_semantic_analyzer_prompt_version_uses_default(cls, value: Any) -> Any:
        return "v1" if value == "" else value

    @field_validator("article_semantic_analyzer_concurrency", mode="before")
    @classmethod
    def blank_article_semantic_analyzer_concurrency_uses_default(cls, value: Any) -> Any:
        return 3 if value == "" else value

    @field_validator("article_semantic_analyzer_timeout_ms", mode="before")
    @classmethod
    def blank_article_semantic_analyzer_timeout_ms_uses_default(cls, value: Any) -> Any:
        return 25000 if value == "" else value

    @model_validator(mode="after")
    def apply_deprecated_rerank_alias(self) -> "Settings":
        if "LEGAL_RAG_LLM_ARTICLE_TOP_K" not in os.environ and "LEGAL_RAG_RERANK_TOP_K" in os.environ:
            self.legal_rag_llm_article_top_k = self.legal_rag_rerank_top_k
        if "LEGAL_RAG_RERANK_TIMEOUT_MS" not in os.environ and "LEGAL_RAG_RERANK_TIMEOUT_SECONDS" in os.environ:
            self.legal_rag_rerank_timeout_ms = int(self.legal_rag_rerank_timeout_seconds) * 1000
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
