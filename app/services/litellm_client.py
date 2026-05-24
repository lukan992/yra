import json
import re
import time
from typing import Any

from loguru import logger

from app.core.config import get_settings
from app.core.logging import log_json
from app.schemas.pipeline import LLMError


class LiteLLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def complete_json(self, prompt: str, model: str) -> dict[str, Any]:
        if not model:
            raise LLMError("LITELLM_MODEL_NOT_CONFIGURED", "LiteLLM model is not configured.")

        last_error: Exception | None = None
        attempts = max(self.settings.litellm_max_retries, 0) + 1
        litellm_model = self._resolve_model(model)

        for attempt in range(1, attempts + 1):
            try:
                from litellm import completion

                started_at = time.perf_counter()
                request_payload = {
                    "model": model,
                    "litellm_model": litellm_model,
                    "attempt": attempt,
                    "prompt_length": len(prompt),
                    "temperature": self.settings.litellm_temperature,
                }
                if self.settings.log_prompts:
                    request_payload["prompt"] = prompt
                log_json("litellm_request", **request_payload)
                response = completion(
                    model=litellm_model,
                    messages=[{"role": "user", "content": prompt}],
                    api_base=self.settings.litellm_base_url or None,
                    api_key=self.settings.litellm_api_key or None,
                    timeout=self.settings.litellm_timeout_seconds,
                    temperature=self.settings.litellm_temperature,
                    response_format={"type": "json_object"},
                )
                content = response.choices[0].message.content or ""
                parsed = self._parse_json(content)
                duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
                log_json(
                    "litellm_response",
                    model=model,
                    litellm_model=litellm_model,
                    attempt=attempt,
                    temperature=self.settings.litellm_temperature,
                    duration_ms=duration_ms,
                    raw_response=content if self.settings.log_prompts else None,
                    parsed_response=parsed if self.settings.log_prompts else None,
                )
                return parsed
            except LLMError as exc:
                last_error = exc
                logger.warning("LiteLLM JSON handling failed")
                log_json(
                    "litellm_error",
                    model=model,
                    litellm_model=litellm_model,
                    attempt=attempt,
                    temperature=self.settings.litellm_temperature,
                    duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    error_code=exc.code,
                    error=exc.message,
                    details=exc.details,
                )
            except Exception as exc:
                last_error = exc
                logger.exception("LiteLLM call failed")
                log_json(
                    "litellm_error",
                    model=model,
                    litellm_model=litellm_model,
                    attempt=attempt,
                    temperature=self.settings.litellm_temperature,
                    duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    error=str(exc),
                )

        if isinstance(last_error, LLMError):
            raise last_error
        raise LLMError("LITELLM_ERROR", "LiteLLM request failed.", {"error": str(last_error)})

    def embed(self, text: str, model: str) -> dict[str, Any]:
        litellm_model = self._resolve_model(model)
        try:
            from litellm import embedding

            response = embedding(
                model=litellm_model,
                input=[text],
                api_base=self.settings.litellm_base_url or None,
                api_key=self.settings.litellm_api_key or None,
                timeout=self.settings.litellm_timeout_seconds,
            )
            item = response.data[0]
            return {"embedding": item["embedding"] if isinstance(item, dict) else item.embedding}
        except Exception as exc:
            log_json("litellm_embedding_error", model=model, litellm_model=litellm_model, error=str(exc))
            raise LLMError("LITELLM_EMBEDDING_ERROR", "LiteLLM embedding request failed.", {"error": str(exc)}) from exc

    def _resolve_model(self, model: str) -> str:
        if "/" in model:
            return model
        if self.settings.litellm_base_url:
            return f"openai/{model}"
        return model

    def _parse_json(self, content: str) -> dict[str, Any]:
        cleaned = content.strip()
        fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.IGNORECASE)
        if fenced:
            cleaned = fenced.group(1).strip()

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMError(
                "LITELLM_JSON_PARSE_ERROR",
                "LiteLLM response is not valid JSON.",
                {"raw_response": content, "error": str(exc)},
            ) from exc

        if not isinstance(parsed, dict):
            raise LLMError("LITELLM_JSON_PARSE_ERROR", "LiteLLM response JSON must be an object.", {"parsed": parsed})
        return parsed
