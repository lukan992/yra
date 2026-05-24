from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = ["act_name", "act_type", "article_number", "article_text", "source_file"]
CONTROL_ARTICLES = {"1", "2", "125", "208", "209", "307", "420", "450.1", "453"}
SOURCE_ARTICLE_RE = re.compile(r"Статья_([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
TEXT_ARTICLE_RE = re.compile(r"Статья\s+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate normalized law articles corpus.")
    parser.add_argument("path", nargs="?", default="../normalized_articles.json")
    return parser.parse_args()


def load_articles(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        articles = [json.loads(line) for line in raw.splitlines() if line.strip()]
        return articles, {}

    parsed = json.loads(raw)
    if isinstance(parsed, list):
        return parsed, {}
    if isinstance(parsed, dict) and isinstance(parsed.get("articles"), list):
        return parsed["articles"], parsed
    raise ValueError("Unsupported corpus format.")


def normalize_article(article: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(article)
    source_file = str(normalized.get("source_file") or "")
    source_match = SOURCE_ARTICLE_RE.search(source_file)
    source_article_number = source_match.group(1) if source_match else ""
    article_number = str(normalized.get("article_number") or "").strip()
    article_title = str(normalized.get("article_title") or "").strip()

    if source_article_number and article_title.isdigit() and article_number:
        merged_number = f"{article_number}.{article_title}"
        if merged_number == source_article_number:
            article_number = source_article_number
            normalized["article_title"] = None

    if not article_number:
        if source_article_number:
            article_number = source_article_number
        else:
            text_match = TEXT_ARTICLE_RE.search(str(normalized.get("article_text") or ""))
            if text_match:
                article_number = text_match.group(1)
    if article_number:
        normalized["article_number"] = article_number
    return normalized


def validate_articles(articles: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if "articles_count" in metadata and metadata["articles_count"] != len(articles):
        errors.append("articles_count does not match actual articles length")

    seen_keys: set[tuple[str, str]] = set()
    present_control_articles: set[str] = set()

    normalized_articles = [normalize_article(article) for article in articles]

    for index, article in enumerate(normalized_articles):
        for field in REQUIRED_FIELDS:
            if not str(article.get(field) or "").strip():
                errors.append(f"article[{index}] missing required field `{field}`")

        if not str(article.get("article_title") or "").strip():
            warnings.append(f"article[{index}] article_title is empty")

        key = (str(article.get("act_name") or "").strip(), str(article.get("article_number") or "").strip())
        if key in seen_keys:
            errors.append(f"duplicate article key: {key[0]} + {key[1]}")
        seen_keys.add(key)

        if key[1] in CONTROL_ARTICLES:
            present_control_articles.add(key[1])

    missing_control_articles = sorted(CONTROL_ARTICLES - present_control_articles)
    if missing_control_articles:
        errors.append(f"missing control articles: {', '.join(missing_control_articles)}")

    return {
        "ok": not errors,
        "articles_checked": len(normalized_articles),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> None:
    args = parse_args()
    path = Path(args.path).resolve()
    articles, metadata = load_articles(path)
    report = validate_articles(articles, metadata)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
