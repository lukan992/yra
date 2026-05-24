from app.repositories.law_repository import LawRepository


def test_build_search_terms_deduplicates() -> None:
    terms = LawRepository._build_search_terms("договор договор обязательства", ["договор", "защита"])
    assert "договор" in terms
    assert "защита" in terms
    assert len(terms) == len(set(term.casefold() for term in terms))


def test_build_search_vector_uses_key_fields() -> None:
    vector = LawRepository.build_search_vector(
        {
            "act_name": "ГК РФ",
            "act_type": "code",
            "section_title": "Общие положения",
            "subsection_title": "Основные положения",
            "chapter_title": "Гражданское законодательство",
            "article_title": "Свобода договора",
            "article_text": "Текст статьи",
        }
    )
    assert "ГК РФ" in vector
    assert "Свобода договора" in vector
