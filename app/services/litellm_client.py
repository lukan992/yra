import json
import re
import time
from typing import Any

from loguru import logger

from app.core.config import get_settings
from app.core.logging import log_json
from app.schemas.pipeline import LLMError


class LiteLLMClient:
    JSON_ONLY_INSTRUCTION = (
        "Return ONLY valid JSON. No markdown. No explanations. No greeting. "
        "No preface. The first character must be `{` or `[`. "
        "If a schema is implied by the user prompt, follow it exactly."
    )

    def __init__(self) -> None:
        self.settings = get_settings()

    def complete_json(
        self,
        prompt: str,
        model: str,
        system_prompt: str | None = None,
        *,
        stage: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        if not model:
            raise LLMError("LITELLM_MODEL_NOT_CONFIGURED", "LiteLLM model is not configured.")

        litellm_model = self._resolve_model(model)
        combined_system_prompt = self._combine_system_prompt(system_prompt)
        raw_response = self._completion_text(
            prompt=prompt,
            model=model,
            litellm_model=litellm_model,
            system_prompt=combined_system_prompt,
            stage=stage,
            timeout_seconds=timeout_seconds,
            attempt_kind="initial",
        )
        try:
            return self._parse_json(raw_response)
        except LLMError as exc:
            log_json(
                "litellm_json_parse_failed",
                stage=stage,
                model=model,
                litellm_model=litellm_model,
                error_code=exc.code,
                error=exc.message,
                details=exc.details,
            )

        log_json(
            "litellm_json_repair_attempt",
            stage=stage,
            model=model,
            litellm_model=litellm_model,
        )
        repair_prompt = self._build_repair_prompt(prompt, raw_response)
        repair_response = self._completion_text(
            prompt=repair_prompt,
            model=model,
            litellm_model=litellm_model,
            system_prompt=self.JSON_ONLY_INSTRUCTION,
            stage=stage,
            timeout_seconds=timeout_seconds,
            attempt_kind="repair",
        )
        try:
            parsed = self._parse_json(repair_response)
            log_json(
                "litellm_json_repair_success",
                stage=stage,
                model=model,
                litellm_model=litellm_model,
            )
            return parsed
        except LLMError as exc:
            log_json(
                "litellm_json_repair_failed",
                stage=stage,
                model=model,
                litellm_model=litellm_model,
                error_code=exc.code,
                error=exc.message,
                details=exc.details,
            )
            raise exc

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

    def _completion_text(
        self,
        *,
        prompt: str,
        model: str,
        litellm_model: str,
        system_prompt: str | None,
        stage: str | None,
        timeout_seconds: int | None,
        attempt_kind: str,
    ) -> str:
        try:
            from litellm import completion

            started_at = time.perf_counter()
            request_payload = {
                "model": model,
                "litellm_model": litellm_model,
                "stage": stage,
                "attempt_kind": attempt_kind,
                "prompt_length": len(prompt),
                "system_prompt_length": len(system_prompt or ""),
                "temperature": self.settings.litellm_temperature,
            }
            if self.settings.log_prompts:
                request_payload["prompt"] = prompt
                request_payload["system_prompt"] = system_prompt
            log_json("litellm_request", **request_payload)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            completion_kwargs: dict[str, Any] = {
                "model": litellm_model,
                "messages": messages,
                "api_base": self.settings.litellm_base_url or None,
                "api_key": self.settings.litellm_api_key or None,
                "timeout": timeout_seconds or self.settings.litellm_timeout_seconds,
                "temperature": self.settings.litellm_temperature,
            }
            if self.settings.litellm_json_response_format_enabled:
                completion_kwargs["response_format"] = {"type": "json_object"}
            response = completion(**completion_kwargs)
            content = response.choices[0].message.content or ""
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            log_json(
                "litellm_response",
                model=model,
                litellm_model=litellm_model,
                stage=stage,
                attempt_kind=attempt_kind,
                temperature=self.settings.litellm_temperature,
                duration_ms=duration_ms,
                raw_response=content if self.settings.log_prompts else None,
                parsed_response=None,
            )
            return content
        except Exception as exc:
            logger.exception("LiteLLM call failed")
            log_json(
                "litellm_error",
                model=model,
                litellm_model=litellm_model,
                stage=stage,
                attempt_kind=attempt_kind,
                temperature=self.settings.litellm_temperature,
                error=str(exc),
            )
            raise LLMError("LITELLM_ERROR", "LiteLLM request failed.", {"error": str(exc), "stage": stage}) from exc

    def _resolve_model(self, model: str) -> str:
        if "/" in model:
            return model
        if self.settings.litellm_base_url:
            return f"openai/{model}"
        return model

    def _combine_system_prompt(self, system_prompt: str | None) -> str:
        if system_prompt:
            return f"{self.JSON_ONLY_INSTRUCTION}\n\n{system_prompt}"
        return self.JSON_ONLY_INSTRUCTION

    @staticmethod
    def _build_repair_prompt(original_prompt: str, raw_response: str) -> str:
        return (
            "Return only corrected valid JSON for the original task.\n"
            "Do not add explanations, markdown, comments or surrounding text.\n\n"
            f"ORIGINAL_TASK:\n{original_prompt}\n\n"
            f"INVALID_RESPONSE:\n{raw_response}"
        )

    def _parse_json(self, content: str) -> dict[str, Any]:
        cleaned = self._extract_json_candidate(content)
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

    def _extract_json_candidate(self, content: str) -> str:
        cleaned = content.strip()
        fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.IGNORECASE)
        if fenced:
            cleaned = fenced.group(1).strip()

        direct = self._first_json_segment(cleaned)
        if direct is not None:
            return direct
        raise LLMError(
            "LITELLM_JSON_PARSE_ERROR",
            "LiteLLM response does not contain JSON.",
            {"raw_response": content},
        )

    @staticmethod
    def _first_json_segment(text: str) -> str | None:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"[\{\[]", text):
            start = match.start()
            try:
                _, end = decoder.raw_decode(text[start:])
            except json.JSONDecodeError:
                continue
            return text[start : start + end]
        return None
