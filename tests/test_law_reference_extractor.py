from app.services.law_reference_extractor import LawReferenceExtractor


def test_reference_extractor_finds_multiple_patterns() -> None:
    service = LawReferenceExtractor()
    article = {"act_name": "ГК РФ", "article_text": "См. статьи 1 и 10, а также пункт 1 статьи 2 и статьями 307 и 309."}
    result = service.extract(article)
    numbers = {item["target_article_number"] for item in result}
    assert {"1", "10", "2", "307", "309"}.issubset(numbers)
