from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from app.core.logging import log_json
from app.repositories.claim_repository import ClaimRepository
from app.repositories.law_repository import LawRepository
from app.schemas.pipeline import LegalContextNotFoundError, PipelineError
from app.schemas.responses import ClaimAnalyzeResponse, ErrorResponse
from app.services.claim_generator import ClaimGenerator
from app.services.claim_validator import ClaimValidator
from app.services.fact_extractor import FactExtractor
from app.services.law_retriever import LawRetriever
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class ClaimPipeline:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.claim_repository = ClaimRepository(db)
        self.law_repository = LawRepository(db)
        self.prompt_loader = PromptLoader()
        self.llm_client = LiteLLMClient()
        self.fact_extractor = FactExtractor(self.llm_client, self.prompt_loader)
        self.law_retriever = LawRetriever(self.law_repository, self.llm_client, self.prompt_loader)
        self.claim_generator = ClaimGenerator(self.llm_client, self.prompt_loader)
        self.claim_validator = ClaimValidator(self.llm_client, self.prompt_loader)

    def run(self, user_text: str) -> ClaimAnalyzeResponse:
        request = self.claim_repository.create_request(user_text)
        run = self.claim_repository.create_run(request.id)

        facts: dict[str, Any] = {}
        used_laws: list[dict[str, Any]] = []
        claim_json: dict[str, Any] | None = None
        validation: dict[str, Any] | None = None

        log_json("pipeline_started", request_id=str(request.id), run_id=str(run.id))

        try:
            facts = self.fact_extractor.extract(user_text)
            self._step(run.id, "fact_extractor", "completed", {"user_text": user_text}, facts)
            case_type = self._string_or_none(facts.get("case_type"))
            self.claim_repository.update_run(run, "facts_extracted", case_type=case_type)

            if facts.get("recommended_status") == "route_to_lawyer":
                self.claim_repository.update_run(run, "route_to_lawyer", case_type=case_type)
                response = self._response("route_to_lawyer", request.id, run.id, facts=facts)
                log_json("pipeline_finished", request_id=str(request.id), run_id=str(run.id), status=response.status)
                return response

            try:
                search_query, used_laws = self.law_retriever.retrieve(user_text, facts)
            except LegalContextNotFoundError as exc:
                self._step(
                    run.id,
                    "law_retriever",
                    "failed",
                    {"user_text": user_text, "facts": facts},
                    error_json={"code": exc.code, "message": exc.message},
                )
                raise

            self._step(
                run.id,
                "law_retriever",
                "completed",
                {"user_text": user_text, "facts": facts},
                {"search_query": search_query, "used_laws": used_laws},
            )
            self.claim_repository.update_run(run, "legal_context_found", case_type=case_type)

            claim_json = self.claim_generator.generate(user_text, facts, used_laws)
            self._step(
                run.id,
                "claim_generator",
                "completed",
                {"user_text": user_text, "facts": facts, "legal_context": used_laws},
                claim_json,
            )

            claim_used_laws = self._extract_used_laws(claim_json, used_laws)
            validation = self.claim_validator.validate(user_text, facts, used_laws, claim_used_laws, claim_json)
            self._step(
                run.id,
                "claim_validator",
                "completed",
                {
                    "user_text": user_text,
                    "facts": facts,
                    "legal_context": used_laws,
                    "used_laws": claim_used_laws,
                    "claim_json": claim_json,
                },
                validation,
            )

            if not validation.get("is_valid", False) or validation.get("recommendation") in {"regenerate", "error"}:
                error = ErrorResponse(code="VALIDATION_FAILED", message="Validator found issues in generated claim.")
                self.claim_repository.update_run(
                    run,
                    "error",
                    case_type=case_type,
                    error_code=error.code,
                    error_message=error.message,
                )
                self.claim_repository.create_generated_claim(request.id, run.id, "error", claim_json, validation, claim_used_laws)
                response = self._response("error", request.id, run.id, facts, claim_used_laws, claim_json, validation, error)
                log_json("pipeline_finished", request_id=str(request.id), run_id=str(run.id), status=response.status)
                return response

            final_status = self._final_status(facts)
            self.claim_repository.update_run(run, final_status, case_type=case_type)
            self.claim_repository.create_generated_claim(request.id, run.id, final_status, claim_json, validation, claim_used_laws)
            response = self._response(final_status, request.id, run.id, facts, claim_used_laws, claim_json, validation)
            log_json("pipeline_finished", request_id=str(request.id), run_id=str(run.id), status=response.status)
            return response

        except PipelineError as exc:
            logger.exception("Pipeline failed")
            self._step(run.id, "pipeline_error", "failed", error_json={"code": exc.code, "message": exc.message, **exc.details})
            self.claim_repository.update_run(
                run,
                "error",
                case_type=self._string_or_none(facts.get("case_type")),
                error_code=exc.code,
                error_message=exc.message,
            )
            response = self._response(
                "error",
                request.id,
                run.id,
                facts=facts,
                used_laws=used_laws,
                claim_json=claim_json,
                validation=validation,
                error=ErrorResponse(code=exc.code, message=exc.message),
            )
            log_json("pipeline_finished", request_id=str(request.id), run_id=str(run.id), status=response.status, error=exc.code)
            return response
        except Exception as exc:
            logger.exception("Unexpected pipeline failure")
            message = "Internal pipeline error."
            self._step(
                run.id,
                "pipeline_error",
                "failed",
                error_json={"code": "INTERNAL_ERROR", "message": message, "details": str(exc)},
            )
            self.claim_repository.update_run(
                run,
                "error",
                case_type=self._string_or_none(facts.get("case_type")),
                error_code="INTERNAL_ERROR",
                error_message=message,
            )
            response = self._response(
                "error",
                request.id,
                run.id,
                facts=facts,
                used_laws=used_laws,
                claim_json=claim_json,
                validation=validation,
                error=ErrorResponse(code="INTERNAL_ERROR", message=message),
            )
            log_json("pipeline_finished", request_id=str(request.id), run_id=str(run.id), status=response.status, error="INTERNAL_ERROR")
            return response

    def _step(
        self,
        run_id: Any,
        name: str,
        status: str,
        input_json: dict[str, Any] | None = None,
        output_json: Any | None = None,
        error_json: dict[str, Any] | None = None,
    ) -> None:
        log_json(
            "pipeline_step",
            run_id=str(run_id),
            step_name=name,
            status=status,
            input=input_json,
            output=output_json,
            error=error_json,
        )
        self.claim_repository.create_step(run_id, name, status, input_json, output_json, error_json)

    def _response(
        self,
        status: str,
        request_id: Any,
        run_id: Any,
        facts: dict[str, Any] | None = None,
        used_laws: list[dict[str, Any]] | None = None,
        claim_json: dict[str, Any] | None = None,
        validation: dict[str, Any] | None = None,
        error: ErrorResponse | None = None,
    ) -> ClaimAnalyzeResponse:
        facts = facts or {}
        return ClaimAnalyzeResponse(
            status=status,
            request_id=str(request_id),
            run_id=str(run_id),
            case_type=self._string_or_none(facts.get("case_type")),
            summary=self._string_or_none(facts.get("summary") or facts.get("problem_summary")),
            facts=facts,
            missing_fields=self._list_or_empty(facts.get("missing_fields")),
            clarifying_questions=self._list_or_empty(facts.get("clarifying_questions")),
            used_laws=used_laws or [],
            claim_json=claim_json,
            validation=validation,
            error=error,
        )

    @staticmethod
    def _final_status(facts: dict[str, Any]) -> str:
        recommended_status = facts.get("recommended_status")
        if recommended_status in {"need_more_info", "route_to_lawyer"}:
            return recommended_status
        missing_fields = facts.get("missing_fields")
        if isinstance(missing_fields, list) and missing_fields:
            return "need_more_info"
        return "claim_generated"

    @staticmethod
    def _extract_used_laws(claim_json: dict[str, Any], legal_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raw_used_laws = claim_json.get("used_laws")
        if isinstance(raw_used_laws, list):
            return raw_used_laws
        return [
            {
                "id": law.get("id"),
                "law_name": law.get("law_name"),
                "article_number": law.get("article_number"),
                "article_title": law.get("article_title"),
            }
            for law in legal_context
        ]

    @staticmethod
    def _list_or_empty(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        return value if isinstance(value, str) else None
