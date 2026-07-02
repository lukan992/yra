import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.logging import log_json
from app.schemas.legal_rag import ArticleSemanticAnalysisSchema
from app.schemas.pipeline import LLMError
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class ArticleSemanticAnalyzer:
    EFFECT_TYPES = {
        "return_principal",
        "return_received",
        "damages_recovery",
        "damages_definition",
        "interest_recovery",
        "delay_liability",
        "termination_or_refusal",
        "termination_consequences",
        "performance_terms",
        "obligation_basis",
        "penalty_or_security",
        "limitation_or_exception",
        "creditor_delay",
        "impossibility",
        "other",
    }
    GENERAL_SCOPE = "general_direct"
    SPECIAL_SCOPE = "special_conditional"

    _CACHE: dict[str, dict[str, Any]] = {}

    def __init__(self, llm_client: LiteLLMClient | None = None, prompt_loader: PromptLoader | None = None) -> None:
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader
        self.settings = get_settings()

    def analyze_many(self, articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not articles:
            return []
        concurrency = max(1, int(self.settings.article_semantic_analyzer_concurrency))
        timeout_ms = max(1000, int(self.settings.article_semantic_analyzer_timeout_ms))
        if concurrency == 1 or len(articles) == 1:
            return [self.analyze(article) for article in articles]

        ordered: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(self.analyze, article): index
                for index, article in enumerate(articles)
            }
            for future in as_completed(futures):
                index = futures[future]
                article = articles[index]
                try:
                    ordered[index] = future.result(timeout=(timeout_ms / 1000.0) + 1.0)
                except TimeoutError:
                    log_json(
                        "legal_rag.article_semantics.timeout",
                        article_number=article.get("article_number"),
                        article_title=article.get("article_title") or article.get("title"),
                        timeout_ms=timeout_ms,
                    )
                    ordered[index] = self._analyze_rule_based(article)
                except Exception:
                    log_json(
                        "legal_rag.article_semantics.failed",
                        article_number=article.get("article_number"),
                        article_title=article.get("article_title") or article.get("title"),
                    )
                    ordered[index] = self._analyze_rule_based(article)
        return [ordered[index] for index in sorted(ordered)]

    def analyze(
        self,
        article: dict[str, Any],
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        llm_result = self._analyze_with_llm(article, request_id=request_id, run_id=run_id)
        if llm_result:
            return llm_result
        return self._analyze_rule_based(article)

    def _analyze_with_llm(
        self,
        article: dict[str, Any],
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.llm_client or not self.prompt_loader:
            return None
        article_text = self._article_text(article)
        if not article_text:
            return None
        model_name = self.settings.article_semantic_analyzer_model or "openai/gpt-4o-mini"
        system_prompt = self.prompt_loader.load("legal/article_semantic_analyzer_system.md")
        cache_key = self._cache_key(article, article_text, model_name)
        cached = self._CACHE.get(cache_key)
        if cached is not None:
            log_json(
                "legal_rag.article_semantics.cache_hit",
                request_id=request_id,
                run_id=run_id,
                article_number=article.get("article_number"),
                article_title=article.get("article_title") or article.get("title"),
                model=model_name,
            )
            return dict(cached)
        log_json(
            "legal_rag.article_semantics.cache_miss",
            request_id=request_id,
            run_id=run_id,
            article_number=article.get("article_number"),
            article_title=article.get("article_title") or article.get("title"),
            model=model_name,
        )
        prompt = self._llm_payload(article, article_text)
        try:
            try:
                raw = self.llm_client.complete_json(
                    prompt,
                    model_name,
                    system_prompt=system_prompt,
                    stage="article_semantic_analyzer",
                    timeout_seconds=max(1, int(self.settings.article_semantic_analyzer_timeout_ms / 1000)),
                )
            except TypeError:
                raw = self.llm_client.complete_json(prompt, model_name)
            model = ArticleSemanticAnalysisSchema.model_validate(raw)
        except (FileNotFoundError, LLMError, ValidationError):
            return None

        result = {
            "article_id": str(article.get("id") or ""),
            "article_number": model.article_number or str(article.get("article_number") or ""),
            "article_title": model.article_title or article.get("article_title") or article.get("title"),
            "semantic_summary": model.summary or self._summary_for_effects(article, [effect.model_dump() for effect in model.legal_effects]),
            "main_institute": model.main_institute,
            "legal_effects": [effect.model_dump() for effect in model.legal_effects],
            "not_covered_effects": [effect.model_dump() for effect in model.not_covered_effects],
            "warnings": list(model.warnings),
            "analysis_source": "llm",
        }
        log_json(
            "legal_rag.article_semantics.llm_output",
            request_id=request_id,
            run_id=run_id,
            step="law_reranking",
            article_number=result.get("article_number"),
            article_title=result.get("article_title"),
            payload=result,
        )
        self._CACHE[cache_key] = dict(result)
        log_json(
            "legal_rag.article_semantics.cache_write",
            request_id=request_id,
            run_id=run_id,
            article_number=article.get("article_number"),
            article_title=article.get("article_title") or article.get("title"),
            model=model_name,
        )
        return result

    def _cache_key(self, article: dict[str, Any], article_text: str, model_name: str) -> str:
        article_id = str(article.get("id") or "")
        content_hash = str(article.get("content_hash") or "")
        updated_at = str(article.get("updated_at") or "")
        if not content_hash:
            content_hash = hashlib.sha256(article_text.encode("utf-8")).hexdigest()
        return "|".join(
            [
                article_id,
                content_hash,
                updated_at,
                self.settings.article_semantic_analyzer_prompt_version,
                model_name,
            ]
        )

    def _analyze_rule_based(self, article: dict[str, Any]) -> dict[str, Any]:
        text = self._article_text(article)
        effects = self._extract_effects(text)
        if not effects:
            effects = [
                {
                    "effect_type": "other",
                    "effect_description": "Статья может иметь общий правовой контекст, но явный эффект для заявленного требования не выделен.",
                    "trigger_conditions": [],
                    "effect_scope": self.GENERAL_SCOPE,
                    "evidence_quote": self._first_sentence(text),
                }
            ]
        return {
            "article_id": str(article.get("id") or ""),
            "article_number": article.get("article_number"),
            "article_title": article.get("article_title") or article.get("title"),
            "semantic_summary": self._summary_for_effects(article, effects),
            "legal_effects": effects,
            "analysis_source": "rule_based",
        }

    @staticmethod
    def _llm_payload(article: dict[str, Any], article_text: str) -> str:
        payload = {
            "act_name": article.get("act_name"),
            "article_number": article.get("article_number"),
            "article_title": article.get("article_title") or article.get("title"),
            "article_text": article_text,
            "article_parts": article.get("article_parts"),
        }
        import json

        return json.dumps(payload, ensure_ascii=False)

    def _extract_effects(self, text: str) -> list[dict[str, Any]]:
        lowered = text.lower()
        effects: list[dict[str, Any]] = []
        has_invalidity = self._has_any(lowered, ("недействительн", "ничтожн", "оспорим"))
        has_unjust = "неосновательн" in lowered
        has_termination = self._has_any(lowered, ("расторжен", "расторжение", "отказ от договора", "отказаться от договора", "прекращение договора", "прекращенном договоре"))
        has_security = self._has_any(lowered, ("задат", "обеспечительный платеж", "обеспечение исполнения"))
        has_guarantee = self._has_any(lowered, ("бенефициар", "гарант", "принципал", "гарантии"))
        has_creditor_delay = self._has_any(lowered, ("кредитор считается просрочившим", "просрочка кредитора"))
        has_impossibility = self._has_any(lowered, ("невозможностью исполнения", "невозможности исполнения"))
        has_money_retention = self._has_any(lowered, ("удержания денежных", "уклонения от их возврата", "просрочки в их уплате", "неправомерного удержания денежных средств"))
        has_delay = self._has_any(lowered, ("просрочивший", "просрочка должника", "просрочкой", "срок исполнения", "утратило интерес"))
        has_breach = self._has_any(lowered, ("неисполн", "ненадлежащ", "нарушен", "просроч"))
        has_license_condition = self._has_any(lowered, ("лиценз", "саморегулируем"))
        has_interest_terms = self._has_any(lowered, ("проценты", "пользование чужими денежными", "денежного обязательства"))
        has_special_indemnity = self._has_any(lowered, ("возмещение потерь", "имущественные потери"))
        has_business_context = self._has_any(lowered, ("предпринимательской деятельности", "предпринимательская деятельность"))
        has_contractual_special_condition = self._has_any(lowered, ("могут своим соглашением", "соглашением предусмотреть", "предусмотреть обязанность"))
        has_return_directive = self._has_any(
            lowered,
            (
                "обязана возвратить",
                "обязан возвратить",
                "возвратить другой все полученное",
                "возвратить все полученное",
                "подлежит возврату",
                "должен быть возвращен",
                "должна быть возвращена",
                "возврат уплаченной суммы",
                "возвратить уплаченную сумму",
                "вернуть уплаченную сумму",
                "возврат денежных средств",
                "возврат оплаты",
                "возврата исполненного",
                "возврат исполненного",
            ),
        )
        has_return_received_context = self._has_any(lowered, ("все полученное", "полученное по сделке", "полученное по договору", "задаток должен быть возвращен"))
        has_damages_terms = self._has_any(lowered, ("возместить убыт", "возмещения убыт", "возмещение убыт", "убытки возмещ", "взысканы убыт", "отвечает перед кредитором за убытки", "возмещение потерь", "имущественные потери")) or (
            "убыт" in lowered and self._has_any(lowered, ("возмест", "возмещен", "возмещ"))
        )
        has_damages_definition = self._has_any(lowered, ("под убытками понимаются", "полного возмещения причиненных ему убытков"))
        has_general_obligation = self._has_any(lowered, ("обязательства должны исполняться", "обязательство должно исполняться"))
        has_performance_terms = self._has_any(lowered, ("надлежащим образом", "срок исполнения", "встречным признается", "исполнение обязательства", "исполнение обязательств"))
        has_penalty = self._has_any(lowered, ("неустойк", "штрафом", "пеней"))
        has_limitation = self._has_any(lowered, ("не допускается", "ограничение размера ответственности", "если иное не предусмотрено"))

        if has_return_directive and self._has_any(
            lowered,
            ("уплачен", "оплат", "денежн", "сумм", "цен", "деньг"),
        ) and not (has_interest_terms and has_money_retention and not has_termination):
            conditions = self._base_conditions(
                has_invalidity=has_invalidity,
                has_unjust=has_unjust,
                has_termination=has_termination,
                has_security=has_security,
            )
            if has_termination:
                conditions.append("termination_consequences_context")
            effects.append(
                self._effect(
                    "return_principal",
                    "Право требовать возврата уплаченной суммы или основной денежной суммы.",
                    conditions,
                    self.SPECIAL_SCOPE if conditions else self.GENERAL_SCOPE,
                    text,
                    (
                        "возврат уплаченной суммы",
                        "возвратить уплаченную сумму",
                        "вернуть уплаченную сумму",
                        "возврат денежных средств",
                        "возврат оплаты",
                        "подлежит возврату",
                    ),
                )
            )

        if has_return_directive and (has_return_received_context or has_invalidity or has_unjust or has_security):
            effects.append(
                self._effect(
                    "return_received",
                    "Право требовать возврата полученного или переданного по специальному основанию.",
                    self._base_conditions(
                        has_invalidity=has_invalidity,
                        has_unjust=has_unjust,
                        has_termination=has_termination,
                        has_security=has_security,
                    ),
                    self.SPECIAL_SCOPE,
                    text,
                    (
                        "возвратить другой все полученное",
                        "возвратить все полученное",
                        "полученное по сделке",
                        "подлежит возврату",
                        "должен быть возвращен",
                    ),
                )
            )

        if has_termination:
            effects.append(
                self._effect(
                    "termination_or_refusal",
                    "Основания или порядок расторжения договора, прекращения обязательства или отказа от договора.",
                    ["termination_or_refusal"],
                    self.SPECIAL_SCOPE,
                    text,
                    ("расторжение", "отказ от договора", "прекращение договора", "расторжен"),
                )
            )

        if has_termination and has_return_directive:
            effects.append(
                self._effect(
                    "termination_consequences",
                    "Последствия расторжения или отказа, включая возврат исполненного или иные последствия прекращения договора.",
                    ["termination_or_refusal"],
                    self.SPECIAL_SCOPE,
                    text,
                    ("последствия расторжения", "при расторжении договора", "прекращенном договоре", "возврат исполненного"),
                )
            )

        if has_damages_definition:
            effects.append(
                self._effect(
                    "damages_definition",
                    "Определение состава убытков и общих правил их исчисления.",
                    [],
                    self.GENERAL_SCOPE,
                    text,
                    ("под убытками понимаются", "полного возмещения причиненных ему убытков"),
                )
            )

        if has_damages_terms:
            conditions = []
            if has_breach:
                conditions.append("breach_or_nonperformance")
            if has_termination:
                conditions.append("termination_or_refusal")
            if has_security:
                conditions.append("deposit_or_security")
            if has_guarantee:
                conditions.append("guarantee_context")
            if has_license_condition:
                conditions.append("regulatory_license_missing")
            if has_impossibility:
                conditions.append("impossibility_context")
            if has_special_indemnity or has_contractual_special_condition:
                conditions.append("special_indemnity_agreement")
            if has_special_indemnity and has_business_context:
                conditions.append("business_context")
            effects.append(
                self._effect(
                    "damages_recovery",
                    "Право требовать возмещения убытков.",
                    conditions,
                    self.SPECIAL_SCOPE if conditions and (has_guarantee or has_license_condition or has_impossibility or has_security or has_termination or has_special_indemnity or has_contractual_special_condition) else self.GENERAL_SCOPE,
                    text,
                    ("возместить убыт", "возмещения убыт", "возмещение убыт", "убытки возмещ", "отвечает перед кредитором за убытки"),
                )
            )

        if has_interest_terms and not has_creditor_delay:
            conditions = ["money_retention_or_delay"] if has_money_retention else []
            effects.append(
                self._effect(
                    "interest_recovery",
                    "Право требовать проценты по денежному обязательству.",
                    conditions,
                    self.SPECIAL_SCOPE if conditions else self.GENERAL_SCOPE,
                    text,
                    ("проценты", "пользование чужими денежными", "удержания денежных", "уклонения от их возврата", "денежного обязательства"),
                )
            )

        if has_creditor_delay:
            effects.append(
                self._effect(
                    "creditor_delay",
                    "Правила о просрочке кредитора и ее последствиях.",
                    ["creditor_delay_context"],
                    self.SPECIAL_SCOPE,
                    text,
                    ("кредитор считается просрочившим", "просрочка кредитора", "не обязан платить проценты"),
                )
            )
            effects.append(
                self._effect(
                    "limitation_or_exception",
                    "Исключение или ограничение последствий денежного обязательства при просрочке кредитора.",
                    ["creditor_delay_context"],
                    self.SPECIAL_SCOPE,
                    text,
                    ("не обязан платить проценты",),
                )
            )

        if has_impossibility:
            effects.append(
                self._effect(
                    "impossibility",
                    "Прекращение обязательства или специальные последствия при невозможности исполнения.",
                    ["impossibility_context"],
                    self.SPECIAL_SCOPE,
                    text,
                    ("невозможностью исполнения", "невозможности исполнения"),
                )
            )

        if has_delay and not has_creditor_delay:
            effects.append(
                self._effect(
                    "delay_liability",
                    "Последствия просрочки или нарушения срока исполнения.",
                    ["missed_deadline_or_delay"],
                    self.SPECIAL_SCOPE,
                    text,
                    ("просрочивший", "просрочка должника", "просрочкой", "срок исполнения", "утратило интерес"),
                )
            )

        if has_general_obligation:
            effects.append(
                self._effect(
                    "obligation_basis",
                    "Общее правило о существовании и надлежащем исполнении обязательства.",
                    [],
                    self.GENERAL_SCOPE,
                    text,
                    ("обязательства должны исполняться", "обязательство должно исполняться"),
                )
            )

        if has_performance_terms:
            conditions = ["performance_deadline"] if "срок исполнения" in lowered else []
            effects.append(
                self._effect(
                    "performance_terms",
                    "Правила надлежащего, встречного или срочного исполнения обязательства.",
                    conditions,
                    self.SPECIAL_SCOPE if conditions else self.GENERAL_SCOPE,
                    text,
                    ("надлежащим образом", "срок исполнения", "встречным признается", "исполнение обязательства", "исполнение обязательств"),
                )
            )

        if has_penalty or has_security:
            conditions = ["deposit_or_security"] if has_security else []
            effects.append(
                self._effect(
                    "penalty_or_security",
                    "Неустойка, задаток или иные обеспечительные механизмы исполнения обязательства.",
                    conditions,
                    self.SPECIAL_SCOPE if conditions else self.GENERAL_SCOPE,
                    text,
                    ("неустойк", "штрафом", "пеней", "задат", "обеспечительный платеж"),
                )
            )

        if has_limitation and not has_creditor_delay:
            effects.append(
                self._effect(
                    "limitation_or_exception",
                    "Ограничение, исключение или специальное условие применения общего правила.",
                    [],
                    self.SPECIAL_SCOPE,
                    text,
                    ("не допускается", "ограничение размера ответственности", "если иное не предусмотрено"),
                )
            )

        return self._dedupe_effects(effects)

    @staticmethod
    def _base_conditions(
        *,
        has_invalidity: bool,
        has_unjust: bool,
        has_termination: bool,
        has_security: bool,
    ) -> list[str]:
        conditions: list[str] = []
        if has_invalidity:
            conditions.append("invalid_transaction")
        if has_unjust:
            conditions.append("unjust_enrichment")
        if has_termination:
            conditions.append("termination_or_refusal")
        if has_security:
            conditions.append("deposit_or_security")
        return conditions

    @staticmethod
    def _article_text(article: dict[str, Any]) -> str:
        parts: list[str] = [
            str(article.get("article_title") or article.get("title") or ""),
            str(article.get("snippet") or ""),
            str(article.get("article_text") or ""),
        ]
        article_parts = article.get("article_parts")
        if isinstance(article_parts, list):
            parts.extend(str(item) for item in article_parts if item)
        elif isinstance(article_parts, dict):
            parts.extend(str(item) for item in article_parts.values() if item)
        return " ".join(" ".join(parts).split()).strip()

    @classmethod
    def _effect(
        cls,
        effect_type: str,
        description: str,
        conditions: list[str],
        effect_scope: str,
        text: str,
        evidence_tokens: tuple[str, ...],
    ) -> dict[str, Any]:
        return {
            "effect_type": effect_type if effect_type in cls.EFFECT_TYPES else "other",
            "effect_description": description,
            "trigger_conditions": list(dict.fromkeys(conditions)),
            "effect_scope": effect_scope if effect_scope in {cls.GENERAL_SCOPE, cls.SPECIAL_SCOPE} else cls.GENERAL_SCOPE,
            "evidence_quote": cls._evidence_quote(text, evidence_tokens),
        }

    @staticmethod
    def _evidence_quote(text: str, tokens: tuple[str, ...]) -> str:
        lowered_tokens = tuple(token.lower() for token in tokens)
        for sentence in re.split(r"(?<=[.!?])\s+|\s+(?=\d+\.)", text):
            cleaned = " ".join(sentence.split()).strip()
            lowered = cleaned.lower()
            if cleaned and any(token in lowered for token in lowered_tokens):
                return cleaned[:280]
        return ArticleSemanticAnalyzer._first_sentence(text)

    @staticmethod
    def _first_sentence(text: str) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return ""
        return re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0][:280]

    @staticmethod
    def _summary_for_effects(article: dict[str, Any], effects: list[dict[str, Any]]) -> str:
        title = str(article.get("article_title") or article.get("title") or "Статья").strip()
        effect_names = ", ".join(str(effect.get("effect_type") or "other") for effect in effects)
        return f"{title}: регулирует {effect_names}."

    @staticmethod
    def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
        return any(token in text for token in tokens)

    @staticmethod
    def _dedupe_effects(effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for effect in effects:
            key = (str(effect.get("effect_type") or ""), str(effect.get("evidence_quote") or ""))
            if key in seen:
                continue
            seen.add(key)
            result.append(effect)
        return result
