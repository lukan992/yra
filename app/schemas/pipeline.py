from typing import Any


class PipelineError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class LegalContextNotFoundError(PipelineError):
    def __init__(self) -> None:
        super().__init__(
            code="LEGAL_CONTEXT_NOT_FOUND",
            message="Не найдены релевантные нормы права в БД. Заполните таблицу law_articles.",
        )


class LLMError(PipelineError):
    pass
