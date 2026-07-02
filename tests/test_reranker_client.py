from app.schemas.pipeline import LLMError
from app.services.reranker_client import RerankerClient


def test_reranker_client_normalizes_multiple_response_formats() -> None:
    parsed_scores = RerankerClient._parse_response([0.9, 0.4])
    parsed_indexed = RerankerClient._parse_response({"results": [{"index": 1, "score": 0.7}]})
    parsed_document_index = RerankerClient._parse_response({"data": [{"document_index": 2, "relevance_score": 0.6}]})
    parsed_scores_object = RerankerClient._parse_response({"scores": [0.9, 0.2]})

    assert parsed_scores == [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.4}]
    assert parsed_indexed == [{"index": 1, "relevance_score": 0.7}]
    assert parsed_document_index == [{"index": 2, "relevance_score": 0.6}]
    assert parsed_scores_object == [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.2}]


def test_reranker_client_builds_payload_formats(monkeypatch) -> None:
    class Settings:
        legal_rag_rerank_payload_format = "texts"

    client = RerankerClient()
    monkeypatch.setattr(client, "settings", Settings())

    payload = client._build_payload("qwen3-reranker-8b", "query", ["a", "b"], top_n=4)

    assert payload["texts"] == ["a", "b"]
    assert payload["top_n"] == 4


def test_reranker_client_http_payload_keeps_raw_rerank_model_name() -> None:
    payload = RerankerClient()._build_payload("qwen3-reranker-8b", "query", ["a"], top_n=2)

    assert payload["model"] == "qwen3-reranker-8b"


def test_reranker_client_raises_config_error_with_remote_litellm_details(monkeypatch) -> None:
    events = []

    class Settings:
        legal_rag_rerank_backend = "litellm"
        litellm_base_url = "http://litellm.local"
        litellm_api_key = None
        legal_rag_rerank_model = "qwen3-reranker-8b"
        legal_rag_rerank_payload_format = "openai_compatible"
        legal_rag_rerank_timeout_ms = 5000
        legal_rag_rerank_timeout_seconds = 5

    class Response:
        ok = False
        status_code = 500
        text = "Unsupported provider: openai. Received Model Group=qwen3-reranker-8b"

        def raise_for_status(self) -> None:
            raise AssertionError("config errors should be converted before raise_for_status")

    def fake_log(event: str, **payload) -> None:
        events.append((event, payload))

    def fake_post(*args, **kwargs):
        return Response()

    client = RerankerClient()
    monkeypatch.setattr(client, "settings", Settings())
    monkeypatch.setattr("app.services.reranker_client.log_json", fake_log)
    monkeypatch.setattr("app.services.reranker_client.requests.post", fake_post)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    try:
        client.rerank("query", [{"act_name": "ГК РФ", "article_text": "text"}], request_id="req-1", run_id="run-1")
    except LLMError as exc:
        assert exc.code == "RERANK_CONFIG_ERROR"
        assert exc.details["provider"] == "openai"
        assert exc.details["model_group"] == "qwen3-reranker-8b"
        assert exc.details["conclusion"] == "remote_litellm_model_group_provider_not_supported_for_rerank"
    else:
        raise AssertionError("expected reranker config error")

    config_error = next(payload for event, payload in events if event == "legal_rag.reranker.config_error")
    assert config_error["requested_model"] == "qwen3-reranker-8b"
    assert config_error["endpoint"] == "http://litellm.local/rerank"
    assert config_error["payload_format"] == "openai_compatible"
    assert config_error["provider"] == "openai"
    assert config_error["model_group"] == "qwen3-reranker-8b"

    try:
        client.rerank("query", [{"act_name": "ГК РФ", "article_text": "text"}], request_id="req-1", run_id="run-1")
    except LLMError as exc:
        assert exc.code == "RERANK_CONFIG_ERROR"
    else:
        raise AssertionError("expected cached reranker config error")

    cached_warning = next(payload for event, payload in events if payload.get("warning") == "reranker_config_error_cached")
    assert cached_warning["provider"] == "openai"


def test_custom_http_backend_uses_rerank_url_and_not_litellm(monkeypatch) -> None:
    events = []

    class Settings:
        legal_rag_rerank_backend = "custom_http"
        legal_rag_rerank_model = "qwen3-reranker-8b"
        legal_rag_rerank_url = "http://127.0.0.1:9000/rerank"
        legal_rag_rerank_payload_format = "openai_compatible"
        legal_rag_rerank_timeout_ms = 4000
        litellm_base_url = "http://litellm.local"
        litellm_api_key = None

    class Response:
        ok = True

        def json(self) -> dict:
            return {"scores": [0.91, 0.13]}

    calls = []

    def fake_log(event: str, **payload) -> None:
        events.append((event, payload))

    def fake_post(url, **kwargs):
        calls.append({"url": url, "json": kwargs["json"], "timeout": kwargs["timeout"]})
        return Response()

    def fail_litellm(*args, **kwargs):
        raise AssertionError("litellm backend should not be used for custom_http")

    client = RerankerClient()
    monkeypatch.setattr(client, "settings", Settings())
    monkeypatch.setattr("app.services.reranker_client.log_json", fake_log)
    monkeypatch.setattr("app.services.reranker_client.requests.post", fake_post)
    monkeypatch.setattr("app.services.reranker_client.LiteLLMRerankerClient.rerank", fail_litellm)

    result = client.rerank("query", [{"act_name": "ГК РФ", "article_text": "a"}, {"act_name": "ГК РФ", "article_text": "b"}], top_n=2)

    assert result == [{"index": 0, "relevance_score": 0.91}, {"index": 1, "relevance_score": 0.13}]
    assert calls[0]["url"] == "http://127.0.0.1:9000/rerank"
    assert calls[0]["json"] == {
        "model": "qwen3-reranker-8b",
        "query": "query",
        "documents": ["ГК РФ\na", "ГК РФ\nb"],
        "top_n": 2,
    }
    assert calls[0]["timeout"] == 4.0
    response_event = next(payload for event, payload in events if event == "legal_rag.reranker.response")
    assert response_event["backend"] == "custom_http"


def test_ollama_backend_without_real_rerank_url_fails_in_controlled_way(monkeypatch) -> None:
    events = []

    class Settings:
        legal_rag_rerank_backend = "ollama"
        legal_rag_rerank_model = "qwen3-reranker-8b"
        legal_rag_rerank_url = ""
        legal_rag_rerank_payload_format = "openai_compatible"
        legal_rag_rerank_timeout_ms = 4000
        ollama_base_url = "http://localhost:11434"

    def fake_log(event: str, **payload) -> None:
        events.append((event, payload))

    client = RerankerClient()
    monkeypatch.setattr(client, "settings", Settings())
    monkeypatch.setattr("app.services.reranker_client.log_json", fake_log)

    try:
        client.rerank("query", [{"act_name": "ГК РФ", "article_text": "a"}], top_n=1)
    except LLMError as exc:
        assert exc.code == "RERANK_BACKEND_UNSUPPORTED"
        assert exc.details["backend"] == "ollama"
    else:
        raise AssertionError("expected controlled ollama failure")

    warning = next(payload for event, payload in events if payload.get("warning") == "reranker_backend_unsupported")
    assert warning["backend"] == "ollama"
