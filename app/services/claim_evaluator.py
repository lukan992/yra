import json
from typing import Any

from app.core.config import get_settings
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class ClaimEvaluator:
    def __init__(self, llm_client: LiteLLMClient, prompt_loader: PromptLoader) -> None:
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader
        self.settings = get_settings()

    def evaluate(self, user_text: str, facts: dict[str, Any], legal_context: list[dict[str, Any]]) -> dict[str, Any]:
        prompt_template = self.prompt_loader.load("claim_evaluator.md")
        prompt = (
            prompt_template.replace("{{USER_TEXT}}", json.dumps(user_text, ensure_ascii=False))
            .replace("{{FACTS}}", json.dumps(facts, ensure_ascii=False))
            .replace("{{LEGAL_CONTEXT}}", json.dumps(legal_context, ensure_ascii=False))
        )
        return self.llm_client.complete_json(prompt, self.settings.litellm_main_model)
