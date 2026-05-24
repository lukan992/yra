from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.session import SessionLocal
from app.repositories.law_repository import LawRepository
from scripts.validate_normalized_law_articles import load_articles, normalize_article, validate_articles


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import normalized law articles into law_articles.")
    parser.add_argument("path", nargs="?", default="../normalized_articles.json")
    return parser.parse_args()


def resolve_status(article_text: str) -> tuple[str, bool]:
    text = article_text.lower()
    if "утратил силу" in text or "утратила силу" in text:
        return "repealed", False
    if "утратили силу" in text:
        return "repealed_group", False
    return "active", True


def build_payload(article: dict[str, Any], repository: LawRepository) -> dict[str, Any]:
    normalized_article = normalize_article(article)
    article_status, is_active = resolve_status(str(normalized_article["article_text"]))
    content_hash = hashlib.sha256(
        json.dumps(normalized_article, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    payload = {
        "act_name": str(normalized_article["act_name"]).strip(),
        "act_type": str(normalized_article["act_type"]).strip(),
        "section_number": normalized_article.get("section_number"),
        "section_title": normalized_article.get("section_title"),
        "subsection_number": normalized_article.get("subsection_number"),
        "subsection_title": normalized_article.get("subsection_title"),
        "chapter_number": normalized_article.get("chapter_number"),
        "chapter_title": normalized_article.get("chapter_title"),
        "article_number": str(normalized_article["article_number"]).strip(),
        "article_title": normalized_article.get("article_title"),
        "article_text": str(normalized_article["article_text"]).strip(),
        "article_parts": normalized_article.get("article_parts"),
        "source_file": str(normalized_article["source_file"]).strip(),
        "article_status": article_status,
        "is_active": is_active,
        "content_hash": content_hash,
        "legal_area": None,
        "tags": None,
        "situations": None,
        "consequences": None,
        "deadlines": None,
        "exceptions": None,
        "related_articles": None,
        "source_url": normalized_article.get("source_url"),
        "edition_date": normalized_article.get("edition_date"),
        "effective_from": normalized_article.get("effective_from"),
        "effective_to": normalized_article.get("effective_to"),
    }
    payload["search_vector"] = repository.build_search_vector(payload)
    return payload


def main() -> None:
    args = parse_args()
    path = Path(args.path).resolve()
    articles, metadata = load_articles(path)
    validation = validate_articles(articles, metadata)
    if not validation["ok"]:
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    db = SessionLocal()
    repository = LawRepository(db)
    report = {
        "total": len(articles),
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "active": 0,
        "repealed": 0,
        "repealed_group": 0,
    }
    try:
        for article in articles:
            try:
                payload = build_payload(article, repository)
                _, created = repository.upsert_article(payload)
                report["inserted" if created else "updated"] += 1
                report[payload["article_status"]] += 1
            except Exception:
                report["failed"] += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
