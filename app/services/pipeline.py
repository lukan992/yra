import re
import time
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import log_json, text_hash, text_preview
from app.repositories.claim_repository import ClaimRepository
from app.repositories.law_repository import LawRepository
from app.schemas.pipeline import LegalContextNotFoundError, PipelineError
from app.schemas.responses import ClaimAnalyzeResponse, ErrorResponse
from app.services.claim_evaluator import ClaimEvaluator
from app.services.claim_generator import ClaimGenerator
from app.services.claim_validator import ClaimValidator
from app.services.fact_extractor import FactExtractor
from app.services.embedding_service import EmbeddingService
from app.services.hybrid_law_retriever import HybridLawRetriever
from app.services.law_graph_expander import LawGraphExpander
from app.services.law_query_builder import LawQueryBuilder
from app.services.law_reference_extractor import LawReferenceExtractor
from app.services.law_reranker import LawReranker
from app.services.legal_guidance_generator import LegalGuidanceGenerator
from app.services.legal_area_classifier import LegalAreaClassifier
from app.services.legal_context_validator import LegalContextValidator
from app.services.law_retriever import LawRetriever
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class ClaimPipeline:
    CONTRACT_DIAGNOSTIC_ARTICLES = {"15", "309", "314", "393", "405", "450", "453"}
    TRACE_COMPACT_EXCLUDED_KEYS = {"article_text", "article_parts", "claim_text", "prompt", "raw_response"}
    TRACE_COMPACT_ARTICLE_KEYS = {
        "id",
        "act_name",
        "article_number",
        "article_title",
        "title",
        "source",
        "keyword_score",
        "vector_score",
        "combined_score",
        "relevance_score",
        "applicability",
        "why_relevant",
        "legal_role",
        "coverage_type",
        "coverage_evidence_quote",
        "coverage_trigger_conditions",
        "semantic_summary",
        "legal_effects",
        "reason",
        "relation_type",
        "source_fragment",
        "snippet",
    }
    TRACE_SUMMARY_EVENTS = {
        "hybrid_law_retrieval": "legal_rag.retriever.summary",
        "law_reranking": "legal_rag.reranker.summary",
        "legal_context_validation": "legal_rag.validator.summary",
    }
    TRACE_RAG_STEPS = {
        "fact_extraction",
        "legal_area_classification",
        "law_query_building",
        "hybrid_law_retrieval",
        "law_reranking",
        "law_graph_expansion",
        "legal_context_validation",
    }

    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.claim_repository = ClaimRepository(db)
        self.law_repository = LawRepository(db)
        self.prompt_loader = PromptLoader()
        self.llm_client = LiteLLMClient()
        self.fact_extractor = FactExtractor(self.llm_client, self.prompt_loader)
        self.legal_area_classifier = LegalAreaClassifier(self.llm_client, self.prompt_loader)
        self.law_query_builder = LawQueryBuilder(self.llm_client, self.prompt_loader)
        self.embedding_service = EmbeddingService()
        self.hybrid_law_retriever = HybridLawRetriever(self.law_repository, self.embedding_service)
        self.law_reranker = LawReranker(self.llm_client, self.prompt_loader)
        self.law_retriever = LawRetriever(self.hybrid_law_retriever, self.law_reranker)
        self.law_graph_expander = LawGraphExpander(self.law_repository, LawReferenceExtractor())
        self.legal_context_validator = LegalContextValidator(self.llm_client, self.prompt_loader)
        self.claim_evaluator = ClaimEvaluator(self.llm_client, self.prompt_loader)
        self.claim_generator = ClaimGenerator(self.llm_client, self.prompt_loader)
        self.claim_validator = ClaimValidator(self.llm_client, self.prompt_loader)
        self.legal_guidance_generator = LegalGuidanceGenerator(self.llm_client, self.prompt_loader)

    def run(self, user_text: str) -> ClaimAnalyzeResponse:
        request = self.claim_repository.create_request(user_text)
        run = self.claim_repository.create_run(request.id)

        facts: dict[str, Any] = {}
        used_laws: list[dict[str, Any]] = []
        claim_json: dict[str, Any] | None = None
        validation: dict[str, Any] | None = None
        evaluation: dict[str, Any] = {}
        guidance: dict[str, Any] | None = None
        legal_area: dict[str, Any] = {}
        query_payload: dict[str, Any] = {}
        retrieval_candidates: list[dict[str, Any]] = []
        context_validation: dict[str, Any] = {}

        request_id = str(request.id)
        run_id = str(run.id)
        user_meta = self._user_text_meta(user_text)

        log_json("pipeline_started", request_id=request_id, run_id=run_id, step="pipeline_started", duration_ms=0.0, **user_meta)

        try:
            started_at = time.perf_counter()
            facts = self.fact_extractor.extract(user_text)
            fact_duration_ms = self._duration_ms(started_at)
            self._step(request_id, run.id, "fact_extraction", "completed", {"user_text": user_meta}, facts, duration_ms=fact_duration_ms)
            self._trace_rag(
                request_id,
                run_id,
                "fact_extraction",
                fact_duration_ms,
                **user_meta,
                extracted_facts=facts,
                case_type=self._string_or_none(facts.get("preliminary_case_type")),
            )
            case_type = self._string_or_none(facts.get("preliminary_case_type"))
            self.claim_repository.update_run(run, "facts_extracted", case_type=case_type)

            started_at = time.perf_counter()
            legal_area = self.legal_area_classifier.classify(user_text, facts)
            legal_area_duration_ms = self._duration_ms(started_at)
            self._step(
                request_id,
                run.id,
                "legal_area_classification",
                "completed",
                {"user_text": user_meta, "facts": facts},
                legal_area,
                duration_ms=legal_area_duration_ms,
            )
            self._trace_rag(
                request_id,
                run_id,
                "legal_area_classification",
                legal_area_duration_ms,
                **user_meta,
                case_type=case_type,
                legal_area=legal_area,
            )

            started_at = time.perf_counter()
            query_payload = self.law_query_builder.build(user_text, facts, legal_area)
            query_duration_ms = self._duration_ms(started_at)
            self._step(
                request_id,
                run.id,
                "law_query_building",
                "completed",
                {"user_text": user_meta, "facts": facts, "legal_area": legal_area},
                query_payload,
                duration_ms=query_duration_ms,
            )
            self._trace_rag(
                request_id,
                run_id,
                "law_query_builder",
                query_duration_ms,
                **user_meta,
                **self.law_query_builder.last_trace,
                expected_acts_normalized=[
                    self.hybrid_law_retriever.normalize_act_name(act_name)
                    for act_name in self._list_or_empty(self.law_query_builder.last_trace.get("expected_acts_raw"))
                    if isinstance(act_name, str)
                ],
                expected_act_types_normalized=[
                    self.hybrid_law_retriever.normalize_act_type(act_type)
                    for act_type in self._list_or_empty(self.law_query_builder.last_trace.get("expected_act_types_raw"))
                    if isinstance(act_type, str)
                ],
            )

            try:
                started_at = time.perf_counter()
                retrieval_candidates, used_laws = self.law_retriever.retrieve(user_text, facts, legal_area, query_payload)
                retrieval_duration_ms = self._duration_ms(started_at)
            except LegalContextNotFoundError as exc:
                self._step(
                    request_id,
                    run.id,
                    "hybrid_law_retrieval",
                    "failed",
                    {"user_text": user_meta, "facts": facts, "legal_area": legal_area, "query_payload": query_payload},
                    error_json={"code": exc.code, "message": exc.message},
                    duration_ms=self._duration_ms(started_at),
                )
                raise

            self._step(
                request_id,
                run.id,
                "hybrid_law_retrieval",
                "completed",
                {"user_text": user_meta, "facts": facts, "legal_area": legal_area, "query_payload": query_payload},
                {"candidate_articles": retrieval_candidates},
                duration_ms=float(self.law_retriever.last_trace.get("hybrid_retrieval_duration_ms") or retrieval_duration_ms),
            )
            self._trace_rag(
                request_id,
                run_id,
                "hybrid_law_retrieval",
                float(self.law_retriever.last_trace.get("hybrid_retrieval_duration_ms") or retrieval_duration_ms),
                **user_meta,
                **self.hybrid_law_retriever.last_trace,
            )
            self._step(
                request_id,
                run.id,
                "law_reranking",
                "completed",
                {"user_text": user_meta, "facts": facts, "legal_area": legal_area},
                {"used_laws": used_laws},
                duration_ms=float(self.law_retriever.last_trace.get("law_reranking_duration_ms") or 0.0),
            )
            self._trace_rag(
                request_id,
                run_id,
                "law_reranking",
                float(self.law_retriever.last_trace.get("law_reranking_duration_ms") or 0.0),
                **user_meta,
                **self.law_reranker.last_trace,
            )
            self._log_reranker_diagnostics(
                request_id,
                run_id,
                float(self.law_retriever.last_trace.get("law_reranking_duration_ms") or 0.0),
                self.law_reranker.last_trace,
            )
            normalized_claims = self._list_or_empty(self.law_reranker.last_trace.get("normalized_claims"))
            if normalized_claims:
                facts["normalized_claims"] = normalized_claims
            self._log_repair_diagnostics(request_id, run_id, self.law_retriever.last_trace)
            started_at = time.perf_counter()
            used_laws = self.law_graph_expander.expand(used_laws, facts=facts, user_text=user_text, normalized_claims=normalized_claims)
            used_laws = self._filter_user_visible_articles(used_laws, request_id, run_id)
            graph_duration_ms = self._duration_ms(started_at)
            self._step(request_id, run.id, "law_graph_expansion", "completed", {"used_laws": used_laws}, used_laws, duration_ms=graph_duration_ms)
            self._trace_rag(
                request_id,
                run_id,
                "law_graph_expansion",
                graph_duration_ms,
                **user_meta,
                **self.law_graph_expander.last_trace,
            )

            started_at = time.perf_counter()
            context_validation = self.legal_context_validator.validate(user_text, facts, legal_area, used_laws)
            context_duration_ms = self._duration_ms(started_at)
            self._step(
                request_id,
                run.id,
                "legal_context_validation",
                "completed",
                {"user_text": user_meta, "facts": facts, "legal_area": legal_area, "legal_context": used_laws},
                context_validation,
                duration_ms=context_duration_ms,
            )
            self._trace_rag(
                request_id,
                run_id,
                "legal_context_validation",
                context_duration_ms,
                **user_meta,
                **self.legal_context_validator.last_trace,
            )
            self._log_expected_articles_diagnostic(request_id, run_id, user_text, facts, legal_area, used_laws)
            self.claim_repository.update_run(run, "legal_context_found", case_type=case_type)

            status = context_validation.get("status")
            if status == "route_to_lawyer":
                response = self._response(
                    "route_to_lawyer",
                    request.id,
                    run.id,
                    facts=facts,
                    used_laws=used_laws,
                    legal_area=legal_area,
                    context_validation=context_validation,
                )
                self.claim_repository.update_run(run, "route_to_lawyer", case_type=self._case_type(facts))
                log_json("pipeline_finished", request_id=request_id, run_id=run_id, step="pipeline_finished", duration_ms=0.0, status=response.status)
                return response
            if status in {"needs_clarification", "insufficient_context", "partial", "blocked_by_missing_facts", "no_coverage"}:
                response = self._response(
                    "need_more_info",
                    request.id,
                    run.id,
                    facts=facts,
                    used_laws=used_laws,
                    legal_area=legal_area,
                    context_validation=context_validation,
                )
                self.claim_repository.update_run(run, "need_more_info", case_type=self._case_type(facts))
                log_json("pipeline_finished", request_id=request_id, run_id=run_id, step="pipeline_finished", duration_ms=0.0, status=response.status)
                return response

            if self._should_generate_guidance(facts, used_laws):
                started_at = time.perf_counter()
                guidance = self.legal_guidance_generator.generate(user_text, facts, used_laws, legal_area)
                self._step(
                    request_id,
                    run.id,
                    "legal_guidance_generation",
                    "completed",
                    {"user_text": user_meta, "facts": facts, "legal_area": legal_area, "legal_context": used_laws},
                    guidance,
                    duration_ms=self._duration_ms(started_at),
                )
                self.claim_repository.update_run(run, "legal_guidance", case_type=self._case_type(facts))
                response = self._response(
                    "legal_guidance",
                    request.id,
                    run.id,
                    facts=facts,
                    used_laws=used_laws,
                    legal_area=legal_area,
                    context_validation=context_validation,
                    guidance=guidance,
                )
                log_json("pipeline_finished", request_id=request_id, run_id=run_id, step="pipeline_finished", duration_ms=0.0, status=response.status)
                return response

            started_at = time.perf_counter()
            evaluation = self.claim_evaluator.evaluate(user_text, facts, used_laws, legal_area)
            self._step(
                request_id,
                run.id,
                "claim_evaluation",
                "completed",
                {"user_text": user_meta, "facts": facts, "legal_area": legal_area, "legal_context": used_laws},
                evaluation,
                duration_ms=self._duration_ms(started_at),
            )

            case_type = self._case_type(facts, evaluation)
            evaluator_status = evaluation.get("status") or evaluation.get("pretrial_claim_status")
            if evaluator_status == "route_to_lawyer":
                self.claim_repository.update_run(run, "route_to_lawyer", case_type=case_type)
                response = self._response(
                    "route_to_lawyer",
                    request.id,
                    run.id,
                    facts=facts,
                    used_laws=used_laws,
                    legal_area=legal_area,
                    context_validation=context_validation,
                    evaluation=evaluation,
                )
                log_json("pipeline_finished", request_id=request_id, run_id=run_id, step="pipeline_finished", duration_ms=0.0, status=response.status)
                return response

            if evaluator_status == "need_more_info":
                self.claim_repository.update_run(run, "need_more_info", case_type=case_type)
                response = self._response(
                    "need_more_info",
                    request.id,
                    run.id,
                    facts=facts,
                    used_laws=used_laws,
                    legal_area=legal_area,
                    context_validation=context_validation,
                    evaluation=evaluation,
                )
                log_json("pipeline_finished", request_id=request_id, run_id=run_id, step="pipeline_finished", duration_ms=0.0, status=response.status)
                return response

            if evaluator_status != "applicable":
                error = ErrorResponse(code="CLAIM_EVALUATION_FAILED", message="Claim evaluator did not approve generation.")
                self.claim_repository.update_run(
                    run,
                    "error",
                    case_type=case_type,
                    error_code=error.code,
                    error_message=error.message,
                )
                response = self._response("error", request.id, run.id, facts=facts, used_laws=used_laws, evaluation=evaluation, error=error)
                log_json("pipeline_finished", request_id=request_id, run_id=run_id, step="pipeline_finished", duration_ms=0.0, status=response.status)
                return response

            started_at = time.perf_counter()
            claim_json = self.claim_generator.generate(user_text, facts, evaluation, used_laws, legal_area)
            self._step(
                request_id,
                run.id,
                "claim_generation",
                "completed",
                {"user_text": user_meta, "facts": facts, "legal_area": legal_area, "legal_context": used_laws},
                claim_json,
                duration_ms=self._duration_ms(started_at),
            )

            claim_used_laws = self._extract_used_laws(claim_json, used_laws)
            started_at = time.perf_counter()
            validation = self.claim_validator.validate(user_text, facts, evaluation, used_laws, claim_used_laws, claim_json)
            self._step(
                request_id,
                run.id,
                "claim_validation",
                "completed",
                {
                    "user_text": user_meta,
                    "facts": facts,
                    "evaluation": evaluation,
                    "legal_context": used_laws,
                    "used_laws": claim_used_laws,
                    "claim_json": claim_json,
                },
                validation,
                duration_ms=self._duration_ms(started_at),
            )

            if not validation.get("is_valid", False) or validation.get("recommendation") != "approve":
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
                log_json("pipeline_finished", request_id=request_id, run_id=run_id, step="pipeline_finished", duration_ms=0.0, status=response.status)
                return response

            self.claim_repository.update_run(run, "claim_generated", case_type=case_type)
            self.claim_repository.create_generated_claim(request.id, run.id, "claim_generated", claim_json, validation, claim_used_laws)
            response = self._response(
                "claim_generated",
                request.id,
                run.id,
                facts,
                claim_used_laws,
                claim_json,
                validation,
                legal_area=legal_area,
                context_validation=context_validation,
            )
            log_json("pipeline_finished", request_id=request_id, run_id=run_id, step="pipeline_finished", duration_ms=0.0, status=response.status)
            return response

        except PipelineError as exc:
            logger.exception("Pipeline failed")
            self._step(request_id, run.id, "pipeline_error", "failed", error_json={"code": exc.code, "message": exc.message, **exc.details}, duration_ms=0.0)
            self.claim_repository.update_run(
                run,
                "error",
                case_type=self._case_type(facts, evaluation),
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
                legal_area=legal_area,
                context_validation=context_validation,
                error=ErrorResponse(code=exc.code, message=exc.message),
                evaluation=evaluation,
                guidance=guidance,
            )
            log_json("pipeline_finished", request_id=request_id, run_id=run_id, step="pipeline_finished", duration_ms=0.0, status=response.status, error=exc.code)
            return response
        except Exception as exc:
            logger.exception("Unexpected pipeline failure")
            message = "Internal pipeline error."
            self._step(
                request_id,
                run.id,
                "pipeline_error",
                "failed",
                error_json={"code": "INTERNAL_ERROR", "message": message, "details": str(exc)},
                duration_ms=0.0,
            )
            self.claim_repository.update_run(
                run,
                "error",
                case_type=self._case_type(facts, evaluation),
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
                legal_area=legal_area,
                context_validation=context_validation,
                error=ErrorResponse(code="INTERNAL_ERROR", message=message),
                evaluation=evaluation,
                guidance=guidance,
            )
            log_json("pipeline_finished", request_id=request_id, run_id=run_id, step="pipeline_finished", duration_ms=0.0, status=response.status, error="INTERNAL_ERROR")
            return response

    def _step(
        self,
        request_id: str,
        run_id: Any,
        name: str,
        status: str,
        input_json: dict[str, Any] | None = None,
        output_json: Any | None = None,
        error_json: dict[str, Any] | None = None,
        duration_ms: float = 0.0,
    ) -> None:
        logged_input = input_json
        logged_output = output_json
        logged_error = error_json
        if self.settings.log_rag_trace and name in self.TRACE_RAG_STEPS:
            logged_input = self._compact_trace_payload(input_json)
            logged_output = self._compact_trace_payload(output_json)
            logged_error = self._compact_trace_payload(error_json)
        log_json(
            "pipeline_step",
            request_id=request_id,
            run_id=str(run_id),
            step=name,
            step_name=name,
            status=status,
            duration_ms=duration_ms,
            input=logged_input,
            output=logged_output,
            error=logged_error,
        )
        self.claim_repository.create_step(run_id, name, status, input_json, output_json, error_json)

    def _trace_rag(
        self,
        request_id: str,
        run_id: str,
        step: str,
        duration_ms: float,
        **payload: Any,
    ) -> None:
        if not self.settings.log_rag_trace:
            return
        compact_payload = self._compact_trace_payload(payload)
        log_json(
            "legal_rag.trace",
            request_id=request_id,
            run_id=run_id,
            step=step,
            duration_ms=duration_ms,
            **compact_payload,
        )
        summary_event = self.TRACE_SUMMARY_EVENTS.get(step)
        if summary_event:
            summary_articles = self._summary_articles_for_step(step, compact_payload)
            if summary_articles:
                log_json(
                    summary_event,
                    request_id=request_id,
                    run_id=run_id,
                    step=step,
                    duration_ms=duration_ms,
                    articles=summary_articles,
                )

    def _log_expected_articles_diagnostic(
        self,
        request_id: str,
        run_id: str,
        user_text: str,
        facts: dict[str, Any],
        legal_area: dict[str, Any],
        legal_context: list[dict[str, Any]],
    ) -> None:
        if not self.settings.log_rag_trace:
            return
        if not self._is_contract_services_scenario(user_text, facts, legal_area):
            return
        present_articles = sorted(
            {
                str(article.get("article_number") or "").strip()
                for article in legal_context
                if str(article.get("article_number") or "").strip()
            }
        )
        missing_articles = sorted(self.CONTRACT_DIAGNOSTIC_ARTICLES.difference(present_articles))
        if not missing_articles:
            return
        log_json(
            "legal_rag.diagnostic.expected_articles_missing",
            request_id=request_id,
            run_id=run_id,
            step="diagnostic",
            duration_ms=0.0,
            present_articles=present_articles,
            missing_articles=missing_articles,
            note="diagnostic only",
        )

    def _log_reranker_diagnostics(
        self,
        request_id: str,
        run_id: str,
        duration_ms: float,
        trace: dict[str, Any],
    ) -> None:
        if not self.settings.log_rag_trace:
            return
        coverage = trace.get("coverage")
        if isinstance(coverage, dict):
            log_json(
                "legal_rag.coverage.roles",
                request_id=request_id,
                run_id=run_id,
                step="law_reranking",
                duration_ms=duration_ms,
                **self._compact_trace_payload(coverage),
            )
            claims = coverage.get("claims")
            if isinstance(claims, dict):
                log_json(
                    "legal_rag.coverage.claims",
                    request_id=request_id,
                    run_id=run_id,
                    step="law_reranking",
                    duration_ms=duration_ms,
                    claims=self._compact_trace_payload(claims),
                )
            missing = coverage.get("missing_coverage")
            if isinstance(missing, dict) and (
                self._list_or_empty(missing.get("claims")) or self._list_or_empty(missing.get("roles"))
            ):
                log_json(
                    "legal_rag.coverage.missing",
                    request_id=request_id,
                    run_id=run_id,
                    step="law_reranking",
                    duration_ms=duration_ms,
                    **self._compact_trace_payload(missing),
                )
        role_corrections = trace.get("role_corrections")
        if isinstance(role_corrections, list):
            for correction in role_corrections:
                if not isinstance(correction, dict):
                    continue
                log_json(
                    "legal_rag.role_corrected",
                    request_id=request_id,
                    run_id=run_id,
                    step="law_reranking",
                    duration_ms=duration_ms,
                    **correction,
                )
        dropped = trace.get("dropped_relevant_candidates")
        if isinstance(dropped, list) and dropped:
            log_json(
                "legal_rag.reranker.dropped_relevant_candidates",
                request_id=request_id,
                run_id=run_id,
                step="law_reranking",
                duration_ms=duration_ms,
                articles=self._compact_trace_payload(dropped),
            )

        semantic_effects = trace.get("semantic_article_effects")
        if isinstance(semantic_effects, list) and semantic_effects:
            log_json(
                "legal_rag.semantic.article_effects",
                request_id=request_id,
                run_id=run_id,
                step="law_reranking",
                duration_ms=duration_ms,
                articles=self._compact_trace_payload(semantic_effects),
            )
        entailment = trace.get("entailment_coverage")
        if isinstance(entailment, list) and entailment:
            log_json(
                "legal_rag.coverage.entailment",
                request_id=request_id,
                run_id=run_id,
                step="law_reranking",
                duration_ms=duration_ms,
                coverage=self._compact_trace_payload(entailment),
            )
        if isinstance(coverage, dict):
            missing_claims = self._list_or_empty(coverage.get("missing_claims"))
            if missing_claims:
                log_json(
                    "legal_rag.coverage.missing_claims",
                    request_id=request_id,
                    run_id=run_id,
                    step="law_reranking",
                    duration_ms=duration_ms,
                    missing_claims=missing_claims,
                )
            blocked = self._list_or_empty(coverage.get("blocked_by_missing_facts"))
            if blocked:
                log_json(
                    "legal_rag.coverage.blocked_by_missing_facts",
                    request_id=request_id,
                    run_id=run_id,
                    step="law_reranking",
                    duration_ms=duration_ms,
                    blocked_by_missing_facts=self._compact_trace_payload(blocked),
                )

    def _log_repair_diagnostics(self, request_id: str, run_id: str, trace: dict[str, Any]) -> None:
        if not self.settings.log_rag_trace:
            return
        repair = trace.get("repair") if isinstance(trace.get("repair"), dict) else {}
        if not repair:
            return
        log_json(
            "legal_rag.repair.started",
            request_id=request_id,
            run_id=run_id,
            step="repair_retrieval",
            duration_ms=0.0,
            missing_claims=self._list_or_empty(repair.get("missing_claims")),
            query_payload=self._compact_trace_payload(repair.get("query_payload")),
        )
        log_json(
            "legal_rag.repair.results",
            request_id=request_id,
            run_id=run_id,
            step="repair_retrieval",
            duration_ms=float(repair.get("duration_ms") or 0.0),
            candidate_count=repair.get("candidate_count"),
            result_ids=self._list_or_empty(repair.get("result_ids")),
        )

    def _filter_user_visible_articles(self, legal_context: list[dict[str, Any]], request_id: str, run_id: str) -> list[dict[str, Any]]:
        has_direct_basis = any(article.get("coverage_type") in {"direct", "valid_conditional"} for article in legal_context)
        result: list[dict[str, Any]] = []
        for article in legal_context:
            coverage_type = str(article.get("coverage_type") or "")
            if coverage_type in {"direct", "valid_conditional"}:
                result.append(article)
            elif coverage_type == "supporting" and has_direct_basis:
                result.append(article)
        if self.settings.log_rag_trace:
            log_json(
                "legal_rag.formatter.user_visible_articles",
                request_id=request_id,
                run_id=run_id,
                step="formatter",
                duration_ms=0.0,
                articles=self._compact_trace_payload(result),
            )
        return result

    @staticmethod
    def _duration_ms(started_at: float) -> float:
        return round((time.perf_counter() - started_at) * 1000, 3)

    @staticmethod
    def _user_text_meta(user_text: str) -> dict[str, Any]:
        return {
            "user_text_preview": text_preview(user_text, limit=300),
            "user_text_hash": text_hash(user_text),
        }

    def _is_contract_services_scenario(
        self,
        user_text: str,
        facts: dict[str, Any],
        legal_area: dict[str, Any],
    ) -> bool:
        scenario = str(self.law_query_builder.last_trace.get("detected_scenario") or "")
        if scenario == "contract_services_nonperformance_refund":
            return True
        text = " ".join(
            [
                str(user_text or ""),
                str(facts.get("summary") or ""),
                str(facts.get("preliminary_case_type") or ""),
                str(legal_area.get("primary_area") or ""),
            ]
        ).lower()
        return all(
            any(token in text for token in group)
            for group in (
                ("договор", "услуг", "исполн", "обязатель"),
                ("неисполн", "срок", "возврат", "деньг", "убыт"),
            )
        )

    def _compact_trace_payload(self, payload: Any) -> Any:
        if self.settings.log_rag_trace_full:
            return payload
        if payload is None:
            return None
        if isinstance(payload, list):
            return [self._compact_trace_payload(item) for item in payload]
        if not isinstance(payload, dict):
            return payload
        if self._looks_like_article(payload):
            return self._compact_article(payload)
        if self._looks_like_facts(payload):
            return self._compact_facts(payload)

        compact: dict[str, Any] = {}
        for key, value in payload.items():
            if key in self.TRACE_COMPACT_EXCLUDED_KEYS:
                continue
            compact[key] = self._compact_trace_payload(value)
        return compact

    @classmethod
    def _compact_article(cls, article: dict[str, Any]) -> dict[str, Any]:
        compact = {key: article.get(key) for key in cls.TRACE_COMPACT_ARTICLE_KEYS if key in article and article.get(key) is not None}
        if "title" not in compact and article.get("article_title"):
            compact["title"] = article.get("article_title")
        if "article_title" not in compact and article.get("title"):
            compact["article_title"] = article.get("title")
        snippet_source = article.get("snippet") or article.get("source_fragment") or article.get("article_text") or ""
        if snippet_source and not compact.get("snippet"):
            compact["snippet"] = text_preview(str(snippet_source), limit=300)
        return compact

    def _compact_facts(self, facts: dict[str, Any]) -> dict[str, Any]:
        parties = facts.get("parties") if isinstance(facts.get("parties"), dict) else {}
        transaction = facts.get("transaction") if isinstance(facts.get("transaction"), dict) else {}
        problem = facts.get("problem") if isinstance(facts.get("problem"), dict) else {}
        demand = facts.get("demand") if isinstance(facts.get("demand"), dict) else {}
        return {
            "summary": self._string_or_none(facts.get("summary")),
            "preliminary_case_type": self._string_or_none(facts.get("preliminary_case_type")),
            "parties_roles": {
                key: value
                for key, value in {
                    "claimant_role": parties.get("claimant_role"),
                    "opponent_role": parties.get("opponent_role"),
                }.items()
                if value
            },
            "transaction": {
                key: value
                for key, value in {
                    "type": transaction.get("item_or_service") or transaction.get("type"),
                    "price": transaction.get("price_amount") or transaction.get("price"),
                    "date": transaction.get("date"),
                }.items()
                if value
            },
            "problem_type": problem.get("type"),
            "demand_type": demand.get("type"),
            "known_facts": self._list_or_empty(facts.get("known_facts")),
            "missing_fields": self._list_or_empty(facts.get("missing_fields")),
            "clarifying_questions": self._list_or_empty(facts.get("clarifying_questions")),
        }

    @classmethod
    def _looks_like_article(cls, payload: dict[str, Any]) -> bool:
        return bool({"article_number", "article_title", "act_name", "keyword_score", "vector_score", "combined_score", "relevance_score"} & set(payload.keys()))

    @staticmethod
    def _looks_like_facts(payload: dict[str, Any]) -> bool:
        return bool({"preliminary_case_type", "parties", "transaction", "problem", "demand", "known_facts", "missing_fields", "clarifying_questions"} & set(payload.keys()))

    @classmethod
    def _summary_articles_for_step(cls, step: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if step == "hybrid_law_retrieval":
            articles = payload.get("merged_candidates") if isinstance(payload.get("merged_candidates"), list) else []
        elif step == "law_reranking":
            articles = payload.get("candidates_after") if isinstance(payload.get("candidates_after"), list) else []
        elif step == "legal_context_validation":
            articles = payload.get("accepted_articles") if isinstance(payload.get("accepted_articles"), list) else []
        else:
            articles = []
        return [cls._compact_summary_article(article) for article in articles if isinstance(article, dict)]

    @staticmethod
    def _compact_summary_article(article: dict[str, Any]) -> dict[str, Any]:
        return {
            "article_number": article.get("article_number"),
            "title": article.get("title") or article.get("article_title"),
            "source": article.get("source"),
            "score": article.get("relevance_score") or article.get("combined_score") or article.get("keyword_score") or article.get("vector_score"),
            "applicability": article.get("applicability"),
        }

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
        evaluation: dict[str, Any] | None = None,
        guidance: dict[str, Any] | None = None,
        legal_area: dict[str, Any] | None = None,
        context_validation: dict[str, Any] | None = None,
    ) -> ClaimAnalyzeResponse:
        facts = facts or {}
        context_validation = context_validation or {}
        missing_fields = self._combined_list(
            facts.get("missing_fields"),
            evaluation.get("missing_required_fields") if evaluation else None,
            guidance.get("missing_fields") if guidance else None,
            context_validation.get("missing_facts"),
            evaluation.get("missing_optional_fields") if evaluation else None,
        )
        clarifying_questions = self._combined_list(
            facts.get("clarifying_questions"),
            evaluation.get("clarifying_questions") if evaluation else None,
            guidance.get("clarifying_questions") if guidance else None,
        )
        return ClaimAnalyzeResponse(
            status=status,
            request_id=str(request_id),
            run_id=str(run_id),
            case_type=self._case_type(facts, evaluation, guidance),
            summary=self._string_or_none(
                (guidance.get("summary") if guidance else None) or facts.get("summary") or facts.get("problem_summary")
            ),
            facts=facts,
            missing_fields=missing_fields,
            clarifying_questions=clarifying_questions,
            used_laws=used_laws or [],
            legal_area=legal_area,
            legal_context_confidence=context_validation.get("confidence"),
            legal_context_warnings=self._list_or_empty(context_validation.get("warnings")),
            guidance=guidance,
            claim_json=claim_json,
            validation=validation,
            error=error,
        )

    @staticmethod
    def _extract_used_laws(claim_json: dict[str, Any], legal_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raw_used_laws = claim_json.get("used_laws")
        if isinstance(raw_used_laws, list):
            normalized = ClaimPipeline._normalize_used_laws(raw_used_laws, legal_context)
            if normalized:
                return normalized
        return [
            {
                "id": str(law.get("id") or f"{law.get('act_name') or law.get('law_name')}-{law.get('article_number') or 'unknown'}"),
                "act_name": law.get("act_name") or law.get("law_name"),
                "act_type": law.get("act_type"),
                "article_number": law.get("article_number"),
                "article_title": law.get("article_title"),
                "article_text": law.get("article_text"),
                "relevance_score": law.get("relevance_score"),
                "applicability": law.get("applicability"),
                "why_relevant": law.get("why_relevant"),
                "regulates": law.get("regulates"),
                "relation_type": law.get("relation_type"),
                "source_file": law.get("source_file"),
            }
            for law in legal_context
            if law.get("act_name") or law.get("law_name") or law.get("article_number")
        ]

    @classmethod
    def _normalize_used_laws(cls, raw_used_laws: list[Any], legal_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        context_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for law in legal_context:
            act_name = str(law.get("act_name") or law.get("law_name") or "").strip()
            article_number = str(law.get("article_number") or "").strip()
            if act_name and article_number:
                context_by_key[(act_name.casefold(), article_number)] = law

        result: list[dict[str, Any]] = []
        for index, item in enumerate(raw_used_laws):
            normalized = cls._normalize_used_law_item(item, context_by_key, index)
            if normalized:
                result.append(normalized)
        return result

    @classmethod
    def _normalize_used_law_item(
        cls,
        item: Any,
        context_by_key: dict[tuple[str, str], dict[str, Any]],
        index: int,
    ) -> dict[str, Any] | None:
        if isinstance(item, dict):
            act_name = cls._string_or_none(item.get("act_name") or item.get("law_name"))
            article_number = cls._string_or_none(item.get("article_number"))
            if not act_name or not article_number:
                return None
            context_match = context_by_key.get((act_name.casefold(), article_number))
            merged = dict(context_match) if context_match else {}
            merged.update(item)
            merged["id"] = str(merged.get("id") or f"{act_name}-{article_number}")
            merged["act_name"] = act_name
            merged["article_number"] = article_number
            return merged

        if isinstance(item, str):
            parsed = cls._parse_used_law_string(item)
            if not parsed:
                return None
            act_name, article_number = parsed
            context_match = context_by_key.get((act_name.casefold(), article_number))
            if context_match:
                merged = dict(context_match)
                merged["id"] = str(merged.get("id") or f"{act_name}-{article_number}")
                return merged
            return {
                "id": f"parsed-law-{index}",
                "act_name": act_name,
                "article_number": article_number,
            }

        return None

    @staticmethod
    def _parse_used_law_string(value: str) -> tuple[str, str] | None:
        text = " ".join(str(value).split()).strip()
        if not text:
            return None
        match = re.search(r"(?P<act>.+?),\s*ст\.?\s*(?P<article>[0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
        if not match:
            return None
        act_name = match.group("act").strip()
        article_number = match.group("article").strip()
        if not act_name or not article_number:
            return None
        return act_name, article_number

    @staticmethod
    def _list_or_empty(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    @classmethod
    def _combined_list(cls, *values: Any) -> list[Any]:
        combined: list[Any] = []
        for value in values:
            combined.extend(cls._list_or_empty(value))
        return combined

    @classmethod
    def _case_type(
        cls,
        facts: dict[str, Any],
        evaluation: dict[str, Any] | None = None,
        guidance: dict[str, Any] | None = None,
    ) -> str | None:
        evaluation = evaluation or {}
        guidance = guidance or {}
        return (
            cls._string_or_none(evaluation.get("case_type"))
            or cls._string_or_none(guidance.get("case_type"))
            or cls._string_or_none(facts.get("preliminary_case_type"))
        )

    @classmethod
    def _should_generate_guidance(cls, facts: dict[str, Any], legal_context: list[dict[str, Any]]) -> bool:
        if not any(law.get("applicability") == "direct" for law in legal_context):
            return True
        case_type = cls._string_or_none(facts.get("preliminary_case_type"))
        if case_type in {
            "defective_goods",
            "defective_service",
            "delivery_delay",
            "service_delay",
            "refund_request",
            "warranty_repair",
            "price_or_payment_dispute",
            "marketplace_dispute",
            "technical_complex_goods",
        }:
            return False

        law_names = {
            law_name.casefold()
            for law in legal_context
            if isinstance((law_name := law.get("law_name")), str)
        }
        return any("трудов" in law_name or "уголов" in law_name for law_name in law_names)

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        return value if isinstance(value, str) else None
