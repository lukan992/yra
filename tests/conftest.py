from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def sample_articles() -> list[dict]:
    return [
        {
            "act_name": "ГК РФ",
            "act_type": "code",
            "section_number": "I",
            "section_title": "Общие положения",
            "subsection_number": "1",
            "subsection_title": "Основные положения",
            "chapter_number": "1",
            "chapter_title": "Гражданское законодательство",
            "article_number": "1",
            "article_title": "Основные начала гражданского законодательства",
            "article_text": "Свобода договора и защита гражданских прав.",
            "article_parts": [{"number": "1", "text": "Свобода договора."}],
            "source_file": "a1.txt",
        },
        {
            "act_name": "ГК РФ",
            "act_type": "code",
            "section_number": "I",
            "section_title": "Общие положения",
            "subsection_number": "2",
            "subsection_title": "Лица",
            "chapter_number": "4",
            "chapter_title": "Юридические лица",
            "article_number": "125",
            "article_title": "Права публично-правовых образований",
            "article_text": "Российская Федерация участвует в отношениях.",
            "article_parts": [{"number": "1", "text": "РФ участвует в отношениях."}],
            "source_file": "a125.txt",
        },
    ]


@pytest.fixture
def temp_json_corpus(tmp_path: Path, sample_articles: list[dict]) -> Path:
    path = tmp_path / "normalized_articles.json"
    payload = {"articles_count": len(sample_articles), "articles": sample_articles}
    path.write_text(__import__("json").dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path
