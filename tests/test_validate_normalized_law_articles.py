import json

from scripts.validate_normalized_law_articles import load_articles, validate_articles


def test_validate_normalized_articles_detects_missing_control_articles(temp_json_corpus) -> None:
    articles, metadata = load_articles(temp_json_corpus)
    report = validate_articles(articles, metadata)

    assert report["ok"] is False
    assert any("missing control articles" in item for item in report["errors"])


def test_validate_normalized_articles_warns_on_empty_title(tmp_path, sample_articles) -> None:
    sample_articles[0]["article_title"] = ""
    payload = {"articles_count": len(sample_articles), "articles": sample_articles}
    path = tmp_path / "normalized_articles.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    articles, metadata = load_articles(path)
    report = validate_articles(articles, metadata)

    assert any("article_title is empty" in item for item in report["warnings"])
