import json
from typing import Any

from app.core.config import get_settings
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class FactExtractor:
    def __init__(self, llm_client: LiteLLMClient, prompt_loader: PromptLoader) -> None:
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader
        self.settings = get_settings()

    def extract(self, user_text: str) -> dict[str, Any]:
        prompt_template = self.prompt_loader.load("fact_extractor.md")
        user_text_json = json.dumps(user_text, ensure_ascii=False)
        prompt = prompt_template.replace("{{USER_TEXT}}", user_text_json).replace("{{ user_text }}", user_text_json)
        return self.llm_client.complete_json(prompt, self.settings.litellm_main_model)
