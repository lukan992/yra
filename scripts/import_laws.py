from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.models import LawArticle
from app.db.session import SessionLocal


ARTICLE_HEADER_RE = re.compile(
    r"Статья\s+([0-9]+(?:\.[0-9]+)?)\.\s*(.+?)\s*$",
    re.IGNORECASE,
)
SECTION_RE = re.compile(r"Раздел\s+([IVXLC0-9]+)", re.IGNORECASE)
CHAPTER_RE = re.compile(r"Глава\s+([IVXLC0-9]+)", re.IGNORECASE)
LAW_NAME_FROM_FILE_RE = re.compile(r"^(.*?)\s+Раздел\s+", re.IGNORECASE)

LAW_NAME_ALIASES = {
    "УК РФ": "Уголовный кодекс Российской Федерации",
    "ТК РФ": "Трудовой кодекс Российской Федерации",
    "ГК РФ": "Гражданский кодекс Российской Федерации",
    "ГПК РФ": "Гражданский процессуальный кодекс Российской Федерации",
    "АПК РФ": "Арбитражный процессуальный кодекс Российской Федерации",
    "КоАП РФ": "Кодекс Российской Федерации об административных правонарушениях",
    "НК РФ": "Налоговый кодекс Российской Федерации",
    "СК РФ": "Семейный кодекс Российской Федерации",
    "УПК РФ": "Уголовно-процессуальный кодекс Российской Федерации",
}


@dataclass
class ParsedLawArticle:
    act_name: str
    article_number: str
    article_title: str
    article_text: str
    tags: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import law article .txt files into the law_articles table."
    )
    parser.add_argument(
        "--source-dir",
        default="../../законы",
        help="Directory with .txt files containing laws. Default: ../../законы relative to this script.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse files and print the result without writing to the database.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_dir = resolve_source_dir(args.source_dir)
    files = sorted(source_dir.glob("*.txt"))
    if not files:
        raise SystemExit(f"No .txt files found in {source_dir}")

    parsed_articles = [parse_law_file(path) for path in files]

    if args.dry_run:
        for article in parsed_articles:
            print(
                f"{article.act_name} | статья {article.article_number} | "
                f"{article.article_title} | tags={article.tags}"
            )
        print(f"Parsed {len(parsed_articles)} files from {source_dir}")
        return

    db = SessionLocal()
    try:
        created, updated = upsert_articles(db, parsed_articles)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(
        f"Imported {len(parsed_articles)} articles from {source_dir}. "
        f"Created: {created}. Updated: {updated}."
    )


def resolve_source_dir(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate

    script_dir = Path(__file__).resolve().parent
    return (script_dir / candidate).resolve()


def parse_law_file(path: Path) -> ParsedLawArticle:
    text = path.read_text(encoding="utf-8-sig")
    lines = [line.strip() for line in text.splitlines()]
    non_empty_lines = [line for line in lines if line]
    if not non_empty_lines:
        raise ValueError(f"{path} is empty")

    act_name = extract_law_name(path)
    article_index, article_number, article_title = extract_article_header(non_empty_lines, path)
    article_text = "\n".join(non_empty_lines[article_index + 1 :]).strip()
    if not article_text:
        raise ValueError(f"Could not extract article text from {path}")

    tags = build_tags(non_empty_lines[:article_index], article_title, act_name)

    return ParsedLawArticle(
        act_name=act_name,
        article_number=article_number,
        article_title=article_title,
        article_text=article_text,
        tags=tags,
    )


def extract_law_name(path: Path) -> str:
    file_stem = path.stem
    match = LAW_NAME_FROM_FILE_RE.match(file_stem)
    short_name = match.group(1).strip() if match else file_stem.strip()
    return LAW_NAME_ALIASES.get(short_name, short_name)


def extract_article_header(lines: list[str], path: Path) -> tuple[int, str, str]:
    for index, line in enumerate(lines):
        match = ARTICLE_HEADER_RE.search(line)
        if match:
            article_number = match.group(1).strip()
            article_title = match.group(2).strip()
            return index, article_number, article_title

    raise ValueError(f"Could not find article header in {path}")


def build_tags(header_lines: list[str], article_title: str, law_name: str) -> list[str]:
    tags: list[str] = [law_name, article_title]

    for line in header_lines:
        section_match = SECTION_RE.search(line)
        if section_match:
            tags.append(f"раздел {section_match.group(1)}")
            continue

        chapter_match = CHAPTER_RE.search(line)
        if chapter_match:
            tags.append(f"глава {chapter_match.group(1)}")

    seen: set[str] = set()
    normalized_tags: list[str] = []
    for tag in tags:
        clean_tag = " ".join(tag.split()).strip()
        if not clean_tag:
            continue
        key = clean_tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized_tags.append(clean_tag)

    return normalized_tags


def upsert_articles(db: Session, parsed_articles: list[ParsedLawArticle]) -> tuple[int, int]:
    created = 0
    updated = 0

    for parsed in parsed_articles:
        existing = (
            db.query(LawArticle)
            .filter(LawArticle.act_name == parsed.act_name)
            .filter(LawArticle.article_number == parsed.article_number)
            .one_or_none()
        )

        if existing is None:
            db.add(
                LawArticle(
                    act_name=parsed.act_name,
                    act_type="code",
                    source_file="legacy_txt_import",
                    article_status="active",
                    content_hash=None,
                    search_vector=" ".join([parsed.act_name, parsed.article_title, parsed.article_text]),
                    article_number=parsed.article_number,
                    article_title=parsed.article_title,
                    article_text=parsed.article_text,
                    tags=parsed.tags,
                    is_active=True,
                )
            )
            created += 1
            continue

        existing.article_title = parsed.article_title
        existing.article_text = parsed.article_text
        existing.tags = parsed.tags
        existing.search_vector = " ".join([parsed.act_name, parsed.article_title, parsed.article_text])
        existing.is_active = True
        updated += 1

    return created, updated


if __name__ == "__main__":
    main()
