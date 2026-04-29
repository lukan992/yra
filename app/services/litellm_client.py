import json
import re
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

        for attempt in range(1, attempts + 1):
            try:
                from litellm import completion

                log_json("litellm_request", model=model, attempt=attempt, prompt=prompt)
                response = completion(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    api_base=self.settings.litellm_base_url or None,
                    api_key=self.settings.litellm_api_key or None,
                    timeout=self.settings.litellm_timeout_seconds,
                    response_format={"type": "json_object"},
                )
                content = response.choices[0].message.content or ""
                parsed = self._parse_json(content)
                log_json("litellm_response", model=model, attempt=attempt, raw_response=content, parsed_response=parsed)
                return parsed
            except LLMError as exc:
                last_error = exc
                logger.warning("LiteLLM JSON handling failed")
                log_json(
                    "litellm_error",
                    model=model,
                    attempt=attempt,
                    error_code=exc.code,
                    error=exc.message,
                    details=exc.details,
                )
            except Exception as exc:
                last_error = exc
                logger.exception("LiteLLM call failed")
                log_json("litellm_error", model=model, attempt=attempt, error=str(exc))

        if isinstance(last_error, LLMError):
            raise last_error
        raise LLMError("LITELLM_ERROR", "LiteLLM request failed.", {"error": str(last_error)})

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
