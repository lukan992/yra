import sys
import types

import pytest

from app.schemas.pipeline import LLMError
from app.services.litellm_client import LiteLLMClient


def test_complete_json_passes_zero_temperature(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Settings:
        litellm_main_model = "openai/test"
        litellm_temperature = 0.0
        litellm_base_url = "http://localhost:4000"
        litellm_api_key = "sk-test"
        litellm_timeout_seconds = 30
        litellm_max_retries = 0
        litellm_json_response_format_enabled = True
        log_prompts = False

    def fake_completion(**kwargs):
        captured.update(kwargs)
        message = types.SimpleNamespace(content='{"ok": true}')
        choice = types.SimpleNamespace(message=message)
        return types.SimpleNamespace(choices=[choice])

    monkeypatch.setattr("app.services.litellm_client.get_settings", lambda: Settings())
    monkeypatch.setattr("app.services.litellm_client.log_json", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(completion=fake_completion))

    client = LiteLLMClient()
    result = client.complete_json('{"task":"test"}', "openai/test", stage="unit")

    assert result == {"ok": True}
    assert captured["temperature"] == 0.0
    assert captured["response_format"] == {"type": "json_object"}


def test_complete_json_repairs_plain_text_response(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class Settings:
        litellm_main_model = "openai/test"
        litellm_temperature = 0.0
        litellm_base_url = "http://localhost:4000"
        litellm_api_key = "sk-test"
        litellm_timeout_seconds = 30
        litellm_max_retries = 0
        litellm_json_response_format_enabled = False
        log_prompts = False

    responses = iter(
        [
            "Готово, вот подтверждение.",
            'prefix {"ok": true, "fixed": true} suffix',
        ]
    )

    def fake_completion(**kwargs):
        calls.append(kwargs)
        message = types.SimpleNamespace(content=next(responses))
        choice = types.SimpleNamespace(message=message)
        return types.SimpleNamespace(choices=[choice])

    monkeypatch.setattr("app.services.litellm_client.get_settings", lambda: Settings())
    monkeypatch.setattr("app.services.litellm_client.log_json", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(completion=fake_completion))

    client = LiteLLMClient()
    result = client.complete_json('{"task":"test"}', "openai/test", stage="fact_extraction")

    assert result == {"ok": True, "fixed": True}
    assert len(calls) == 2
    assert "response_format" not in calls[0]
    assert "INVALID_RESPONSE" in calls[1]["messages"][-1]["content"]


def test_complete_json_raises_after_failed_repair(monkeypatch) -> None:
    class Settings:
        litellm_main_model = "openai/test"
        litellm_temperature = 0.0
        litellm_base_url = "http://localhost:4000"
        litellm_api_key = "sk-test"
        litellm_timeout_seconds = 30
        litellm_max_retries = 0
        litellm_json_response_format_enabled = False
        log_prompts = False

    def fake_completion(**kwargs):
        message = types.SimpleNamespace(content="still not json")
        choice = types.SimpleNamespace(message=message)
        return types.SimpleNamespace(choices=[choice])

    monkeypatch.setattr("app.services.litellm_client.get_settings", lambda: Settings())
    monkeypatch.setattr("app.services.litellm_client.log_json", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(completion=fake_completion))

    client = LiteLLMClient()
    with pytest.raises(LLMError) as exc_info:
        client.complete_json('{"task":"test"}', "openai/test", stage="law_query_building")

    assert exc_info.value.code == "LITELLM_JSON_PARSE_ERROR"
