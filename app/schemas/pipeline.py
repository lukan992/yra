from typing import Any


class PipelineError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class LegalContextNotFoundError(PipelineError):
    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="LEGAL_CONTEXT_NOT_FOUND",
            message=message or "Не найдены релевантные нормы права в БД.",
            details=details,
        )


class QueryBuilderError(PipelineError):
    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="QUERY_BUILDER_EMPTY_PAYLOAD",
            message=message or "LawQueryBuilder не сформировал валидный поисковый запрос.",
            details=details,
        )


class LLMError(PipelineError):
    pass


class EmbeddingUnavailableError(PipelineError):
    pass
