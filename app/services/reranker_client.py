import os
import re
import time
from typing import Any

import requests
from loguru import logger

from app.core.config import Settings, get_settings
from app.core.logging import log_json
from app.schemas.pipeline import LLMError


class BaseRerankerBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        *,
        top_n: int | None = None,
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @property
    def backend_name(self) -> str:
        raise NotImplementedError

    @property
    def endpoint(self) -> str:
        raise NotImplementedError

    def _timeout_seconds(self) -> float:
        return max(1, int(self.settings.legal_rag_rerank_timeout_ms)) / 1000.0

    def _log_input(
        self,
        *,
        model: str,
        endpoint: str,
        query: str,
        document_texts: list[str],
        payload: dict[str, Any] | None,
        payload_format_name: str,
        request_id: str | None,
        run_id: str | None,
    ) -> None:
        log_json(
            "legal_rag.reranker.input",
            request_id=request_id,
            run_id=run_id,
            step="law_reranking",
            backend=self.backend_name,
            model=model,
            endpoint=endpoint,
            query_length=len(query),
            documents_count=len(document_texts),
            first_document_length=len(document_texts[0]) if document_texts else 0,
            payload_format_name=payload_format_name,
            top_level_field_names=sorted(payload.keys()) if isinstance(payload, dict) else [],
        )

    def _log_success(
        self,
        items: list[dict[str, Any]],
        duration_ms: float,
        model: str,
        request_id: str | None,
        run_id: str | None,
    ) -> None:
        log_json(
            "legal_rag.reranker.response",
            request_id=request_id,
            run_id=run_id,
            step="law_reranking",
            backend=self.backend_name,
            model=model,
            endpoint=self.endpoint,
            duration_ms=duration_ms,
            result_count=len(items),
            top_scores=items[:8],
        )

    def _log_warning(
        self,
        *,
        model: str,
        warning: str,
        error: str | None = None,
        http_status: int | None = None,
        body_preview: str | None = None,
        documents_count: int,
        query_length: int,
        request_id: str | None,
        run_id: str | None,
    ) -> None:
        log_json(
            "legal_rag.reranker.warning",
            request_id=request_id,
            run_id=run_id,
            step="law_reranking",
            backend=self.backend_name,
            model=model,
            endpoint=self.endpoint,
            warning=warning,
            error=error,
            http_status=http_status,
            body_preview=body_preview,
            payload_format=self.settings.legal_rag_rerank_payload_format,
            documents_count=documents_count,
            query_length=query_length,
        )

    @staticmethod
    def _document_text(document: dict[str, Any]) -> str:
        parts = [
            str(document.get("act_name") or ""),
            f"статья {document.get('article_number')}" if document.get("article_number") else "",
            str(document.get("article_title") or document.get("title") or ""),
            str(document.get("chapter_title") or ""),
            str(document.get("snippet") or document.get("article_text") or "")[:1800],
        ]
        return "\n".join(part for part in parts if part).strip()

    @classmethod
    def _build_payload(cls, model: str, query: str, documents: list[str], *, top_n: int | None, payload_format: str) -> dict[str, Any]:
        if payload_format == "cohere":
            return {
                "model": model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
                "return_documents": False,
            }
        if payload_format == "texts":
            return {
                "model": model,
                "query": query,
                "texts": documents,
                "top_n": top_n,
            }
        return {
            "model": model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }

    @classmethod
    def _parse_response(cls, response: Any) -> list[dict[str, Any]]:
        if response is None:
            return []
        if isinstance(response, list):
            results = response
        elif isinstance(response, dict):
            if isinstance(response.get("scores"), list):
                results = response["scores"]
            else:
                results = response.get("results") or response.get("data") or response.get("items") or []
        else:
            results = getattr(response, "results", None) or getattr(response, "data", None) or []

        parsed: list[dict[str, Any]] = []
        if isinstance(results, list) and results and all(isinstance(item, (float, int)) for item in results):
            return [{"index": index, "relevance_score": float(score)} for index, score in enumerate(results)]

        for idx, item in enumerate(results if isinstance(results, list) else []):
            normalized = cls._normalize_result_item(item, idx)
            if normalized is not None:
                parsed.append(normalized)
        return parsed

    @staticmethod
    def _normalize_result_item(item: Any, fallback_index: int) -> dict[str, Any] | None:
        if isinstance(item, dict):
            index = item.get("index", item.get("document_index", item.get("id", fallback_index)))
            score = item.get("relevance_score", item.get("score", item.get("relevance", 0.0)))
            if index is None and item.get("text") is not None:
                index = fallback_index
            if index is None:
                return None
            return {"index": int(index), "relevance_score": float(score or 0.0)}
        index = getattr(item, "index", getattr(item, "document_index", fallback_index))
        score = getattr(item, "relevance_score", getattr(item, "score", getattr(item, "relevance", 0.0)))
        if index is None:
            return None
        return {"index": int(index), "relevance_score": float(score or 0.0)}

    @staticmethod
    def _request_cache_key(*, request_id: str | None, run_id: str | None) -> str | None:
        if run_id:
            return f"run:{run_id}"
        if request_id:
            return f"request:{request_id}"
        return None


class LiteLLMRerankerClient(BaseRerankerBackend):
    def __init__(self, settings: Settings, cached_config_errors: dict[str, dict[str, Any]]) -> None:
        super().__init__(settings)
        self._cached_config_errors = cached_config_errors

    @property
    def backend_name(self) -> str:
        return "litellm"

    @property
    def endpoint(self) -> str:
        if self.settings.litellm_base_url:
            return f"{self.settings.litellm_base_url.rstrip('/')}/rerank"
        return "litellm.rerank"

    def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        *,
        top_n: int | None = None,
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not documents:
            return []
        model = self.settings.legal_rag_rerank_model
        if not model:
            raise LLMError("RERANK_MODEL_NOT_CONFIGURED", "Reranker model is not configured.")

        cache_key = self._request_cache_key(request_id=request_id, run_id=run_id)
        cached_error = self._cached_config_errors.get(cache_key) if cache_key else None
        if cached_error:
            log_json(
                "legal_rag.reranker.warning",
                request_id=request_id,
                run_id=run_id,
                step="law_reranking",
                backend=self.backend_name,
                model=model,
                endpoint=cached_error.get("endpoint"),
                warning="reranker_config_error_cached",
                payload_format=self.settings.legal_rag_rerank_payload_format,
                http_status=cached_error.get("http_status"),
                provider=cached_error.get("provider"),
                model_group=cached_error.get("model_group"),
                conclusion=cached_error.get("conclusion"),
                documents_count=len(documents),
                query_length=len(query),
            )
            raise LLMError(
                "RERANK_CONFIG_ERROR",
                "Remote LiteLLM model group provider is not supported for rerank.",
                cached_error,
            )

        started_at = time.perf_counter()
        try:
            if self.settings.litellm_base_url and "PYTEST_CURRENT_TEST" not in os.environ:
                return self._rerank_via_http(
                    query,
                    documents,
                    top_n=top_n,
                    request_id=request_id,
                    run_id=run_id,
                    model=model,
                    started_at=started_at,
                )
            from litellm import rerank

            document_texts = [self._document_text(document) for document in documents]
            payload = self._build_payload(
                model,
                query,
                document_texts,
                top_n=top_n,
                payload_format="litellm_native",
            )
            self._log_input(
                model=model,
                endpoint=self.endpoint,
                query=query,
                document_texts=document_texts,
                payload=payload,
                payload_format_name="litellm_native",
                request_id=request_id,
                run_id=run_id,
            )
            response = rerank(
                model=self._resolve_model(model),
                query=query,
                documents=document_texts,
                top_n=top_n,
                return_documents=False,
                api_base=None,
                api_key=self.settings.litellm_api_key or None,
                timeout=self._timeout_seconds(),
            )
            items = self._parse_response(response)
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self._log_success(items, duration_ms, model, request_id, run_id)
            return items
        except LLMError:
            raise
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.warning("Reranker call failed: {}", exc)
            self._log_warning(
                model=model,
                warning="reranker_unavailable",
                error=str(exc),
                documents_count=len(documents),
                query_length=len(query),
                request_id=request_id,
                run_id=run_id,
            )
            raise LLMError(
                "RERANK_ERROR",
                "Reranker request failed.",
                {"error": str(exc), "model": model, "backend": self.backend_name, "duration_ms": duration_ms},
            ) from exc

    def _rerank_via_http(
        self,
        query: str,
        documents: list[dict[str, Any]],
        *,
        top_n: int | None,
        request_id: str | None,
        run_id: str | None,
        model: str,
        started_at: float,
    ) -> list[dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if self.settings.litellm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.litellm_api_key}"
        document_texts = [self._document_text(document) for document in documents]
        payload = self._build_payload(
            model,
            query,
            document_texts,
            top_n=top_n,
            payload_format=self.settings.legal_rag_rerank_payload_format,
        )
        self._log_input(
            model=model,
            endpoint=self.endpoint,
            query=query,
            document_texts=document_texts,
            payload=payload,
            payload_format_name=self.settings.legal_rag_rerank_payload_format,
            request_id=request_id,
            run_id=run_id,
        )
        response = requests.post(
            self.endpoint,
            headers=headers,
            json=payload,
            timeout=self._timeout_seconds(),
        )
        if not response.ok:
            body = response.text[:500]
            details = self._parse_config_error(body)
            self._log_warning(
                model=model,
                warning="reranker_http_error",
                http_status=response.status_code,
                body_preview=body,
                documents_count=len(document_texts),
                query_length=len(query),
                request_id=request_id,
                run_id=run_id,
            )
            if details:
                error_details = {
                    "requested_model": model,
                    "endpoint": self.endpoint,
                    "payload_format": self.settings.legal_rag_rerank_payload_format,
                    "http_status": response.status_code,
                    "provider": details.get("provider"),
                    "model_group": details.get("model_group"),
                    "body_preview": body,
                    "conclusion": "remote_litellm_model_group_provider_not_supported_for_rerank",
                }
                cache_key = self._request_cache_key(request_id=request_id, run_id=run_id)
                if cache_key:
                    self._cached_config_errors[cache_key] = error_details
                log_json(
                    "legal_rag.reranker.config_error",
                    request_id=request_id,
                    run_id=run_id,
                    step="law_reranking",
                    backend=self.backend_name,
                    model=model,
                    requested_model=model,
                    endpoint=self.endpoint,
                    payload_format=self.settings.legal_rag_rerank_payload_format,
                    http_status=response.status_code,
                    documents_count=len(document_texts),
                    query_length=len(query),
                    provider=details.get("provider"),
                    model_group=details.get("model_group"),
                    conclusion=error_details["conclusion"],
                )
                raise LLMError(
                    "RERANK_CONFIG_ERROR",
                    "Remote LiteLLM model group provider is not supported for rerank.",
                    error_details,
                )
            response.raise_for_status()
        payload_data = response.json()
        items = self._parse_response(payload_data)
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        self._log_success(items, duration_ms, model, request_id, run_id)
        return items

    def _resolve_model(self, model: str) -> str:
        if "/" in model:
            return model
        if self.settings.litellm_base_url:
            return f"openai/{model}"
        return model

    @staticmethod
    def _parse_config_error(body: str) -> dict[str, str] | None:
        if "Unsupported provider" not in body or "Received Model Group" not in body:
            return None
        provider_match = re.search(r"Unsupported provider:\s*([A-Za-z0-9_\-/.]+)", body)
        model_group_match = re.search(r"Received Model Group=([A-Za-z0-9_\-/.]+)", body)
        return {
            "provider": provider_match.group(1).rstrip(" .,:;") if provider_match else "unknown",
            "model_group": model_group_match.group(1) if model_group_match else "unknown",
        }


class HttpRerankerClient(BaseRerankerBackend):
    backend = "custom_http"

    @property
    def backend_name(self) -> str:
        return self.backend

    @property
    def endpoint(self) -> str:
        return str(self.settings.legal_rag_rerank_url or "").strip()

    def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        *,
        top_n: int | None = None,
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not documents:
            return []
        model = self.settings.legal_rag_rerank_model
        if not model:
            raise LLMError("RERANK_MODEL_NOT_CONFIGURED", "Reranker model is not configured.")
        if not self.endpoint:
            self._log_warning(
                model=model,
                warning="reranker_url_missing",
                error=f"{self.backend_name}_backend_requires_legal_rag_rerank_url",
                documents_count=len(documents),
                query_length=len(query),
                request_id=request_id,
                run_id=run_id,
            )
            raise LLMError(
                "RERANK_BACKEND_NOT_CONFIGURED",
                "Reranker backend URL is not configured.",
                {"backend": self.backend_name, "model": model},
            )

        started_at = time.perf_counter()
        headers = {"Content-Type": "application/json"}
        document_texts = [self._document_text(document) for document in documents]
        payload = self._build_payload(
            model,
            query,
            document_texts,
            top_n=top_n,
            payload_format=self.settings.legal_rag_rerank_payload_format,
        )
        self._log_input(
            model=model,
            endpoint=self.endpoint,
            query=query,
            document_texts=document_texts,
            payload=payload,
            payload_format_name=self.settings.legal_rag_rerank_payload_format,
            request_id=request_id,
            run_id=run_id,
        )
        try:
            response = requests.post(
                self.endpoint,
                headers=headers,
                json=payload,
                timeout=self._timeout_seconds(),
            )
            if not response.ok:
                body = response.text[:500]
                self._log_warning(
                    model=model,
                    warning="reranker_http_error",
                    http_status=response.status_code,
                    body_preview=body,
                    documents_count=len(document_texts),
                    query_length=len(query),
                    request_id=request_id,
                    run_id=run_id,
                )
                response.raise_for_status()
            payload_data = response.json()
            items = self._parse_response(payload_data)
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self._log_success(items, duration_ms, model, request_id, run_id)
            return items
        except LLMError:
            raise
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.warning("Reranker call failed: {}", exc)
            self._log_warning(
                model=model,
                warning="reranker_unavailable",
                error=str(exc),
                documents_count=len(document_texts),
                query_length=len(query),
                request_id=request_id,
                run_id=run_id,
            )
            raise LLMError(
                "RERANK_ERROR",
                "Reranker request failed.",
                {"error": str(exc), "model": model, "backend": self.backend_name, "duration_ms": duration_ms},
            ) from exc


class OllamaRerankerClient(HttpRerankerClient):
    backend = "ollama"

    def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        *,
        top_n: int | None = None,
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.endpoint:
            model = self.settings.legal_rag_rerank_model
            self._log_warning(
                model=model,
                warning="reranker_backend_unsupported",
                error="ollama_backend_requires_real_rerank_url",
                documents_count=len(documents),
                query_length=len(query),
                request_id=request_id,
                run_id=run_id,
            )
            raise LLMError(
                "RERANK_BACKEND_UNSUPPORTED",
                "Ollama backend requires a real rerank HTTP endpoint and does not use chat scoring.",
                {"backend": self.backend_name, "model": model},
            )
        return super().rerank(query, documents, top_n=top_n, request_id=request_id, run_id=run_id)


class RerankerClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._cached_config_errors: dict[str, dict[str, Any]] = {}

    def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        *,
        top_n: int | None = None,
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        backend = self._make_backend()
        return backend.rerank(query, documents, top_n=top_n, request_id=request_id, run_id=run_id)

    def _make_backend(self) -> BaseRerankerBackend:
        backend_name = str(self.settings.legal_rag_rerank_backend or "litellm").strip().lower()
        if backend_name == "custom_http":
            return HttpRerankerClient(self.settings)
        if backend_name == "ollama":
            return OllamaRerankerClient(self.settings)
        return LiteLLMRerankerClient(self.settings, self._cached_config_errors)

    def _build_payload(self, model: str, query: str, documents: list[str], *, top_n: int | None) -> dict[str, Any]:
        return BaseRerankerBackend._build_payload(
            model,
            query,
            documents,
            top_n=top_n,
            payload_format=self.settings.legal_rag_rerank_payload_format,
        )

    @classmethod
    def _parse_response(cls, response: Any) -> list[dict[str, Any]]:
        return BaseRerankerBackend._parse_response(response)
