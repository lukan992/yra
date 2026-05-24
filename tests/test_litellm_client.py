import sys
import types

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
    result = client.complete_json('{"task":"test"}', "openai/test")

    assert result == {"ok": True}
    assert captured["temperature"] == 0.0
