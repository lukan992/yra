from app.services.law_graph_expander import LawGraphExpander


class StubRepository:
    def __init__(self) -> None:
        self.saved = []

    def replace_references(self, source_article_id: str, references: list[dict]) -> None:
        self.saved.append((source_article_id, references))

    def get_active_article(self, act_name: str, article_number: str) -> dict | None:
        if article_number == "10":
            return {"id": "10", "act_name": act_name, "article_number": "10", "article_title": "Пределы", "article_text": "..."}
        return None


class StubExtractor:
    def extract(self, article: dict) -> list[dict]:
        return [{"target_act_name": "ГК РФ", "target_article_number": "10", "relation_type": "explicit_reference", "source_fragment": "статья 10"}]


def test_graph_expander_adds_only_explicit_targets() -> None:
    repo = StubRepository()
    service = LawGraphExpander(repo, StubExtractor())
    result = service.expand([{"id": "1", "act_name": "ГК РФ", "article_number": "1"}])
    assert len(result) == 2
    assert result[1]["relation_type"] == "explicit_reference"
