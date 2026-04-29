from functools import lru_cache

from typing import Any

from pydantic import Field, field_validator
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
    litellm_main_model: str = Field(default="", alias="LITELLM_MAIN_MODEL")
    litellm_validator_model: str = Field(default="", alias="LITELLM_VALIDATOR_MODEL")
    litellm_timeout_seconds: int = Field(default=120, alias="LITELLM_TIMEOUT_SECONDS")
    litellm_max_retries: int = Field(default=2, alias="LITELLM_MAX_RETRIES")

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

    @field_validator("litellm_max_retries", mode="before")
    @classmethod
    def blank_litellm_retries_uses_default(cls, value: Any) -> Any:
        return 2 if value == "" else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
