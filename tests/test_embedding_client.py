import io
import json
from urllib.error import URLError

import pytest

from app.core.config import get_settings
from app.schemas.pipeline import EmbeddingUnavailableError
from app.services.embedding_client import OllamaEmbeddingClient


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.buffer = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self, *args, **kwargs):
        return self.buffer.read(*args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


def test_embedding_client_calls_ollama(monkeypatch) -> None:
    vector = [0.1] * get_settings().embedding_dim

    def fake_urlopen(request, timeout):
        return FakeResponse({"embeddings": [vector]})

    monkeypatch.setattr("app.services.embedding_client.urlopen", fake_urlopen)
    client = OllamaEmbeddingClient()
    result = client.embed("текст")
    assert result == vector


def test_embedding_client_rejects_wrong_dimension(monkeypatch) -> None:
    vector = [0.1] * 3

    def fake_urlopen(request, timeout):
        return FakeResponse({"embeddings": [vector]})

    monkeypatch.setattr("app.services.embedding_client.urlopen", fake_urlopen)
    client = OllamaEmbeddingClient()
    with pytest.raises(EmbeddingUnavailableError) as exc:
        client.embed("текст")
    assert exc.value.code == "EMBEDDING_DIMENSION_MISMATCH"


def test_embedding_client_raises_when_ollama_unavailable(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr("app.services.embedding_client.urlopen", fake_urlopen)
    client = OllamaEmbeddingClient()
    with pytest.raises(EmbeddingUnavailableError) as exc:
        client.embed("текст")
    assert exc.value.code == "OLLAMA_UNAVAILABLE"
