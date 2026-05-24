from scripts.import_normalized_law_articles import build_payload, resolve_status


class StubRepository:
    @staticmethod
    def build_search_vector(payload: dict) -> str:
        return " ".join(filter(None, [payload.get("act_name"), payload.get("article_title"), payload.get("article_text")]))


def test_resolve_status_marks_repealed() -> None:
    status, is_active = resolve_status("Статья утратила силу.")
    assert status == "repealed"
    assert is_active is False


def test_build_payload_sets_hash_and_search_vector(sample_articles) -> None:
    payload = build_payload(sample_articles[0], StubRepository())

    assert payload["act_name"] == "ГК РФ"
    assert payload["article_status"] == "active"
    assert payload["is_active"] is True
    assert payload["content_hash"]
    assert "Свобода договора" in payload["search_vector"]
