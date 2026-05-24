from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import get_settings
from app.core.logging import log_json
from app.schemas.pipeline import EmbeddingUnavailableError


class OllamaEmbeddingClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def embed(self, text: str) -> list[float]:
        embeddings = self.embed_batch([text])
        return embeddings[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        cleaned = [str(text).strip() for text in texts if str(text).strip()]
        if not cleaned:
            raise EmbeddingUnavailableError("EMBEDDING_INPUT_EMPTY", "Embedding input must contain at least one non-empty text.")

        payload = {"model": self.settings.embedding_model, "input": cleaned}
        data = self._post("/api/embed", payload)
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(cleaned):
            raise EmbeddingUnavailableError(
                "EMBEDDING_INVALID_RESPONSE",
                "Ollama embedding response did not contain the expected number of vectors.",
                {"response_keys": list(data.keys()) if isinstance(data, dict) else []},
            )

        return [self._validate_vector(item) for item in embeddings]

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.settings.ollama_base_url.rstrip('/')}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started_at = time.perf_counter()
        log_json(
            "ollama_embedding_request",
            model=self.settings.embedding_model,
            path=path,
            input_count=len(payload.get("input") or []),
        )
        try:
            with urlopen(request, timeout=self.settings.embedding_timeout_seconds) as response:
                data = json.load(response)
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            raise EmbeddingUnavailableError(
                "OLLAMA_EMBEDDING_HTTP_ERROR",
                "Ollama embedding request failed.",
                {"status": exc.code, "details": details},
            ) from exc
        except URLError as exc:
            raise EmbeddingUnavailableError(
                "OLLAMA_UNAVAILABLE",
                "Ollama embedding service is unavailable.",
                {"error": str(exc)},
            ) from exc
        except TimeoutError as exc:
            raise EmbeddingUnavailableError(
                "OLLAMA_TIMEOUT",
                "Ollama embedding request timed out.",
                {"error": str(exc)},
            ) from exc
        except json.JSONDecodeError as exc:
            raise EmbeddingUnavailableError(
                "OLLAMA_INVALID_JSON",
                "Ollama embedding response is not valid JSON.",
                {"error": str(exc)},
            ) from exc

        if not isinstance(data, dict):
            raise EmbeddingUnavailableError(
                "OLLAMA_INVALID_RESPONSE",
                "Ollama embedding response must be a JSON object.",
            )
        if data.get("error"):
            raise EmbeddingUnavailableError(
                "OLLAMA_EMBEDDING_ERROR",
                "Ollama reported an embedding error.",
                {"error": data.get("error")},
            )
        log_json(
            "ollama_embedding_response",
            model=self.settings.embedding_model,
            path=path,
            input_count=len(payload.get("input") or []),
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return data

    def _validate_vector(self, value: Any) -> list[float]:
        if not isinstance(value, list) or not value:
            raise EmbeddingUnavailableError(
                "EMBEDDING_INVALID_VECTOR",
                "Ollama embedding response contained an empty or invalid vector.",
            )
        vector = [float(item) for item in value]
        if len(vector) != self.settings.embedding_dim:
            raise EmbeddingUnavailableError(
                "EMBEDDING_DIMENSION_MISMATCH",
                "Ollama embedding dimension does not match configured EMBEDDING_DIM.",
                {"expected": self.settings.embedding_dim, "actual": len(vector)},
            )
        return vector
