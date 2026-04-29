import json
from typing import Any

from app.core.config import get_settings
from app.core.logging import log_json
from app.repositories.law_repository import LawRepository
from app.schemas.pipeline import LegalContextNotFoundError
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class LawRetriever:
    def __init__(self, law_repository: LawRepository, llm_client: LiteLLMClient, prompt_loader: PromptLoader) -> None:
        self.law_repository = law_repository
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader
        self.settings = get_settings()

    def retrieve(self, user_text: str, facts: dict[str, Any], top_k: int = 5) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not self.law_repository.has_active_articles():
            log_json("law_retrieval_error", reason="law_articles_empty")
            raise LegalContextNotFoundError()

        prompt_template = self.prompt_loader.load("law_query_builder.md")
        prompt = (
            prompt_template.replace("{{USER_TEXT}}", json.dumps(user_text, ensure_ascii=False))
            .replace("{{FACTS}}", json.dumps(facts, ensure_ascii=False))
        )
        search_query = self.llm_client.complete_json(prompt, self.settings.litellm_main_model)
        query = self._build_query(search_query, facts, user_text)
        tags = search_query.get("tags") if isinstance(search_query.get("tags"), list) else []
        laws = self.law_repository.search(query=query, tags=tags, top_k=top_k)
        if not laws:
            log_json("law_retrieval_error", reason="no_matching_articles", search_query=search_query)
            raise LegalContextNotFoundError()
        return search_query, laws

    @staticmethod
    def _build_query(search_query: dict[str, Any], facts: dict[str, Any], user_text: str) -> str:
        if search_query.get("query"):
            return str(search_query["query"])

        query_parts: list[str] = []
        if search_query.get("main_query"):
            query_parts.append(str(search_query["main_query"]))

        alternative_queries = search_query.get("alternative_queries")
        if isinstance(alternative_queries, list):
            query_parts.extend(str(query) for query in alternative_queries if query)

        if not query_parts:
            query_parts.append(str(facts.get("summary") or user_text))

        return " ".join(query_parts)
