from __future__ import annotations

import json
from typing import Any

from app.services.embedding_client import OllamaEmbeddingClient


class EmbeddingService:
    def __init__(self, client: OllamaEmbeddingClient | None = None) -> None:
        self.client = client or OllamaEmbeddingClient()

    def build_article_text(self, article: dict[str, Any]) -> str:
        lines = [str(article.get("act_name") or "").strip()]
        self._append_section(lines, "Раздел", article.get("section_number"), article.get("section_title"))
        self._append_section(lines, "Подраздел", article.get("subsection_number"), article.get("subsection_title"))
        self._append_section(lines, "Глава", article.get("chapter_number"), article.get("chapter_title"))

        article_header = self._join_parts("Статья", article.get("article_number"), article.get("article_title"))
        if article_header:
            lines.extend(["", article_header])

        article_text = str(article.get("article_text") or "").strip()
        if article_text:
            lines.extend(["", article_text])

        self._append_label(lines, "Правовая область", article.get("legal_area"))
        self._append_label(lines, "Теги", article.get("tags"))
        self._append_label(lines, "Типовые ситуации", article.get("situations"))
        self._append_label(lines, "Сроки", article.get("deadlines"))
        self._append_label(lines, "Исключения", article.get("exceptions"))
        return "\n".join(line for line in lines if line is not None).strip()

    def embed_text(self, text: str) -> list[float]:
        return self.client.embed(text)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.client.embed_batch(texts)

    @staticmethod
    def _append_section(lines: list[str], prefix: str, number: Any, title: Any) -> None:
        section = EmbeddingService._join_parts(prefix, number, title)
        if section:
            lines.append(section)

    @staticmethod
    def _append_label(lines: list[str], label: str, value: Any) -> None:
        normalized = EmbeddingService._normalize_value(value)
        if normalized:
            lines.append(f"{label}: {normalized}")

    @staticmethod
    def _join_parts(prefix: str, number: Any, title: Any) -> str:
        left = str(number).strip() if number is not None and str(number).strip() else ""
        right = str(title).strip() if title is not None and str(title).strip() else ""
        if not left and not right:
            return ""
        if left and right:
            return f"{prefix} {left}. {right}".strip()
        return f"{prefix} {left or right}".strip()

    @staticmethod
    def _normalize_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return str(value).strip()
