from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.init_db import init_db
from scripts.import_laws import (
    parse_law_file,
    resolve_source_dir,
    upsert_articles,
)
from app.db.session import SessionLocal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create YRA database tables and optionally import law articles "
            "from .txt files."
        )
    )
    parser.add_argument(
        "--with-laws",
        action="store_true",
        help="Import laws after creating tables.",
    )
    parser.add_argument(
        "--source-dir",
        default="../../законы",
        help=(
            "Directory with .txt files containing laws. "
            "Default: ../../законы relative to this script."
        ),
    )
    return parser.parse_args()


def import_laws_from_directory(source_dir: str) -> None:
    resolved_dir = resolve_source_dir(source_dir)
    files = sorted(resolved_dir.glob("*.txt"))
    if not files:
        raise SystemExit(f"No .txt files found in {resolved_dir}")

    parsed_articles = [parse_law_file(path) for path in files]

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
        f"Imported {len(parsed_articles)} articles from {resolved_dir}. "
        f"Created: {created}. Updated: {updated}."
    )


def main() -> None:
    args = parse_args()
    init_db()

    if args.with_laws:
        import_laws_from_directory(args.source_dir)


if __name__ == "__main__":
    main()
