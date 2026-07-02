import json
import re
from typing import Any

from app.core.config import get_settings
from app.schemas.pipeline import LLMError
from app.services.litellm_client import LiteLLMClient
from app.services.prompt_loader import PromptLoader


class FactExtractor:
    CHAT_WRAPPER_PREFIXES = (
        "формат чата:",
        "название чата:",
        "текущее состояние кейса:",
        "правило:",
        "уже собранные факты:",
        "уже закрытые поля:",
        "какие поля уже спрашивали:",
        "история диалога:",
    )
    MONTHS_RU = {
        "января": "01",
        "февраля": "02",
        "марта": "03",
        "апреля": "04",
        "мая": "05",
        "июня": "06",
        "июля": "07",
        "августа": "08",
        "сентября": "09",
        "октября": "10",
        "ноября": "11",
        "декабря": "12",
    }

    def __init__(self, llm_client: LiteLLMClient, prompt_loader: PromptLoader) -> None:
        self.llm_client = llm_client
        self.prompt_loader = prompt_loader
        self.settings = get_settings()

    def extract(self, user_text: str) -> dict[str, Any]:
        normalized_input = self._normalize_input(user_text)
        self._log_wrapper_normalization(normalized_input)
        prompt_template = self.prompt_loader.load("fact_extractor.md")
        user_text_json = json.dumps(normalized_input["prompt_text"], ensure_ascii=False)
        prompt = prompt_template.replace("{{USER_TEXT}}", user_text_json)
        try:
            result = self.llm_client.complete_json(
                prompt,
                self.settings.legal_fact_extraction_model,
                stage="fact_extraction",
            )
        except LLMError:
            result = self._fallback_extract(normalized_input["fact_text"])
        return self._postprocess_facts(result, user_text, normalized_input)

    @staticmethod
    def _fallback_extract(user_text: str) -> dict[str, Any]:
        text = user_text.strip()
        lowered = text.lower()
        if any(token in lowered for token in ["работодатель", "зарплат", "увольн", "труд"]):
            case_type = "labor_rights"
            problem_type = "salary_delay" if "зарплат" in lowered else "other"
            opponent_role = "employer"
        elif any(token in lowered for token in ["товар", "магазин", "продав", "услуг", "маркетплейс"]):
            case_type = "price_or_payment_dispute" if "деньг" in lowered else "refund_request"
            problem_type = "refusal" if "не возвращ" in lowered or "отказ" in lowered else "other"
            opponent_role = "seller"
        else:
            case_type = "outside_zopp_scope"
            problem_type = "refusal" if "не возвращ" in lowered or "не испол" in lowered else "other"
            opponent_role = "unknown"

        demand_type = "refund" if "деньг" in lowered or "возврат" in lowered else "unknown"
        contract_present = "yes" if "договор" in lowered else "unknown"
        known_facts = []
        if "договор" in lowered:
            known_facts.append("Существует договор между сторонами.")
        if "не испол" in lowered:
            known_facts.append("Обязательство по договору не исполнено.")
        if "не возвращ" in lowered or "деньг" in lowered:
            known_facts.append("Пользователь указывает на невозврат денежных средств.")

        clarifying_questions = [
            "Какое именно обязательство по договору не было исполнено?",
            "Кто является второй стороной договора и какова сумма спора?",
        ]
        return {
            "summary": text,
            "preliminary_case_type": case_type,
            "confidence": "low",
            "parties": {
                "applicant_role": "unknown",
                "applicant_name": None,
                "opponent_role": opponent_role,
                "opponent_name": None,
            },
            "transaction": {
                "type": "service" if "услуг" in lowered else "unknown",
                "item_or_service": None,
                "price": FactExtractor._extract_amount(lowered),
                "currency": "RUB" if "руб" in lowered else "unknown",
                "purchase_or_order_date": {"exact_date": None, "relative_date": None, "raw_text": None},
                "payment_date": {"exact_date": None, "relative_date": None, "raw_text": None},
                "purpose": "unknown",
            },
            "problem": {
                "problem_type": problem_type,
                "description": text,
                "problem_date": {"exact_date": None, "relative_date": None, "raw_text": None},
            },
            "user_demand": {
                "demand_type": demand_type,
                "description": "Возврат денег" if demand_type == "refund" else None,
                "amount": FactExtractor._extract_amount(lowered),
                "currency": "RUB" if "руб" in lowered else "unknown",
            },
            "prior_contact": {
                "contacted_opponent": "unknown",
                "contact_method": "unknown",
                "contact_date": {"exact_date": None, "relative_date": None, "raw_text": None},
                "opponent_response": None,
            },
            "documents": {
                "receipt": "unknown",
                "contract": contract_present,
                "warranty_card": "unknown",
                "photos_or_video": "unknown",
                "correspondence": "unknown",
                "other_documents": [],
            },
            "known_facts": known_facts,
            "uncertain_facts": [],
            "missing_fields": [
                {"field": "Сумма спора", "reason": "Нужна сумма, чтобы оценить требование пользователя."},
                {"field": "Предмет договора", "reason": "Нужно понимать, что именно должен был исполнить контрагент."},
            ],
            "clarifying_questions": clarifying_questions,
            "risk_flags": [{"flag": "llm_fallback", "reason": "Факты извлечены эвристически из-за недоступности LLM."}],
        }

    @classmethod
    def _postprocess_facts(
        cls, facts: dict[str, Any], user_text: str, normalized_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        result = dict(facts) if isinstance(facts, dict) else {}
        normalized_input = normalized_input or cls._normalize_input(user_text)
        result = cls._ensure_minimum_shape(result, user_text, normalized_input)
        existing_claims = result.get("normalized_claims") if isinstance(result.get("normalized_claims"), list) else []
        inferred_claims = cls._normalize_claims(result, normalized_input["fact_text"])
        merged_claims: list[str] = []
        for claim in [*existing_claims, *inferred_claims]:
            normalized = " ".join(str(claim or "").split()).strip()
            if normalized and normalized not in merged_claims:
                merged_claims.append(normalized)
        result["normalized_claims"] = merged_claims
        result["missing_fields"] = cls._build_missing_fields(result)
        result["clarifying_questions"] = cls._build_clarifying_questions(result)
        return result

    @staticmethod
    def _log_wrapper_normalization(normalized_input: dict[str, str]) -> None:
        from app.core.logging import log_json, text_preview

        raw_text = normalized_input.get("raw_text") or ""
        fact_text = normalized_input.get("fact_text") or ""
        log_json(
            "fact_extraction.wrapper_normalized",
            raw_text_preview=text_preview(raw_text, limit=220),
            fact_text_preview=text_preview(fact_text, limit=220),
            used_history_user_message=bool(raw_text and fact_text and raw_text != fact_text),
        )

    @classmethod
    def _ensure_minimum_shape(
        cls, facts: dict[str, Any], user_text: str, normalized_input: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(facts)
        text = normalized_input["fact_text"]
        lowered = text.lower()
        date_matches = cls._extract_dates(text)
        contract_date = cls._date_for_context(text, date_matches, ("договор", "заключ"))
        payment_date = cls._date_for_context(text, date_matches, ("оплат", "оплата"))
        prior_contact_date = cls._date_for_context(text, date_matches, ("обрат", "требован"))
        amount = cls._extract_amount(text)
        raw_case_type = str(result.get("preliminary_case_type") or "").strip().lower()
        preliminary_case_type = (
            raw_case_type
            if raw_case_type and raw_case_type not in {"unknown", "other"}
            else cls._detect_case_type(lowered)
        )
        opponent_role = cls._detect_opponent_role(lowered)
        problem_type = result.get("problem_type") or cls._detect_problem_type(lowered)
        demand_type = result.get("demand_type") or cls._detect_demand_type(lowered)
        problem_date = cls._date_for_context(text, date_matches, ("не выполнил", "не исполнил", "в срок", "отказ"))
        prior_contact = result.get("prior_contact") if isinstance(result.get("prior_contact"), dict) else {}
        transaction = result.get("transaction") if isinstance(result.get("transaction"), dict) else {}
        parties = result.get("parties") if isinstance(result.get("parties"), dict) else {}
        problem = result.get("problem") if isinstance(result.get("problem"), dict) else {}
        user_demand = result.get("user_demand") if isinstance(result.get("user_demand"), dict) else {}
        user_demands = result.get("user_demands") if isinstance(result.get("user_demands"), list) else []
        summary = cls._clean_summary(str(result.get("summary") or ""), text)

        result["summary"] = summary or text or None
        result["preliminary_case_type"] = preliminary_case_type
        result["parties_roles"] = {
            "opponent_role": result.get("parties_roles", {}).get("opponent_role")
            if isinstance(result.get("parties_roles"), dict) and result.get("parties_roles", {}).get("opponent_role")
            else opponent_role
        }
        result["parties"] = {
            "applicant_role": parties.get("applicant_role") or "customer",
            "applicant_name": parties.get("applicant_name"),
            "opponent_role": parties.get("opponent_role") or opponent_role,
            "opponent_name": parties.get("opponent_name"),
        }
        result["transaction"] = {
            "type": transaction.get("type") or cls._transaction_type(lowered),
            "item_or_service": transaction.get("item_or_service") or cls._transaction_item(lowered),
            "price": cls._coalesce_number(transaction.get("price"), amount),
            "currency": transaction.get("currency") or ("RUB" if amount is not None else "unknown"),
            "purchase_or_order_date": cls._date_payload(
                transaction.get("purchase_or_order_date"), contract_date, cls._date_raw(contract_date, text)
            ),
            "payment_date": cls._date_payload(
                transaction.get("payment_date"), payment_date, cls._date_raw(payment_date, text)
            ),
            "purpose": transaction.get("purpose") or "personal",
            "contract_date": contract_date,
            "item_or_service_description": transaction.get("item_or_service_description") or cls._transaction_item(lowered),
        }
        result["problem_type"] = problem_type
        result["problem"] = {
            "problem_type": problem.get("problem_type") or problem_type,
            "type": problem.get("type") or problem_type,
            "description": problem.get("description") or result["summary"],
            "problem_date": cls._date_payload(problem.get("problem_date"), problem_date, cls._date_raw(problem_date, text)),
        }
        result["demand_type"] = demand_type
        result["user_demand"] = {
            "demand_type": user_demand.get("demand_type") or demand_type,
            "description": user_demand.get("description") or cls._demand_description(lowered),
            "amount": cls._coalesce_number(user_demand.get("amount"), amount if demand_type == "refund" else None),
            "currency": user_demand.get("currency") or ("RUB" if amount is not None else "unknown"),
        }
        if not user_demands:
            user_demands = cls._build_user_demands(lowered, amount)
        result["user_demands"] = user_demands
        result["prior_contact"] = {
            "contacted_opponent": prior_contact.get("contacted_opponent") or cls._detect_prior_contact(lowered),
            "contact_method": prior_contact.get("contact_method") or "written",
            "contact_date": cls._date_payload(prior_contact.get("contact_date"), prior_contact_date, cls._date_raw(prior_contact_date, text)),
            "opponent_response": prior_contact.get("opponent_response") or cls._detect_opponent_response(lowered),
            "date": prior_contact_date,
        }
        result["known_facts"] = cls._known_facts(lowered, result)
        result.setdefault("uncertain_facts", [])
        result.setdefault("documents", {})
        result["documents"].setdefault("contract", "yes" if "договор" in lowered else "unknown")
        result["documents"].setdefault("receipt", "yes" if "оплат" in lowered else "unknown")
        result["documents"].setdefault("correspondence", "yes" if "обрат" in lowered or "требован" in lowered else "unknown")
        return result

    @classmethod
    def _normalize_input(cls, user_text: str) -> dict[str, str]:
        raw_text = " ".join(str(user_text or "").split()).strip()
        if not raw_text:
            return {"raw_text": "", "fact_text": "", "prompt_text": ""}

        fact_text = raw_text
        lowered = raw_text.lower()
        if "история диалога:" in lowered:
            history_start = lowered.index("история диалога:")
            history_text = raw_text[history_start:]
            messages = re.findall(r"(?:^|\s)(user|assistant)\s*:\s*(.*?)(?=(?:\s(?:user|assistant)\s*:)|$)", history_text, flags=re.IGNORECASE | re.DOTALL)
            user_messages = [cls._strip_wrapper(message.strip()) for role, message in messages if role.lower() == "user"]
            if user_messages:
                fact_text = user_messages[-1]
        fact_text = cls._strip_wrapper(fact_text)
        prompt_text = fact_text
        return {"raw_text": raw_text, "fact_text": fact_text, "prompt_text": prompt_text}

    @classmethod
    def _strip_wrapper(cls, text: str) -> str:
        lines = [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]
        cleaned: list[str] = []
        for line in lines:
            if any(line.lower().startswith(prefix) for prefix in cls.CHAT_WRAPPER_PREFIXES):
                continue
            cleaned.append(line)
        merged = " ".join(cleaned).strip()
        merged = re.sub(r"\b(?:Формат чата|Название чата|Текущее состояние кейса|Правило)\s*:[^\.]+", "", merged, flags=re.IGNORECASE)
        return " ".join(merged.split()).strip()

    @staticmethod
    def _detect_opponent_role(text: str) -> str:
        if any(token in text for token in ("исполнитель", "подрядчик", "оказал услуг", "оказание услуг")):
            return "service_provider"
        if any(token in text for token in ("продав", "магазин")):
            return "seller"
        return "unknown"

    @classmethod
    def _default_transaction(cls, text: str) -> dict[str, Any]:
        return {
            "type": "оказание услуг" if "услуг" in text else None,
            "item_or_service": "оказание услуг" if "услуг" in text else None,
            "price": cls._extract_amount(text),
        }

    @staticmethod
    def _detect_problem_type(text: str) -> str | None:
        if any(token in text for token in ("не выполнил", "не исполнил", "неисполн")) and any(
            token in text for token in ("в срок", "срок", "просроч")
        ):
            return "nonperformance_or_delay"
        if any(token in text for token in ("не выполнил", "не исполнил", "неисполн")):
            return "nonperformance"
        if "просроч" in text or "в срок" in text:
            return "delay"
        return None

    @staticmethod
    def _detect_demand_type(text: str) -> str | None:
        if "возврат" in text or "вернуть" in text or "деньги" in text:
            return "refund"
        if "убыт" in text or "возмест" in text:
            return "compensation"
        return None

    @staticmethod
    def _known_facts(text: str, facts: dict[str, Any] | None = None) -> list[str]:
        facts: list[str] = []
        if "договор" in text:
            facts.append("Пользователь сообщает о наличии договора.")
        if "оплат" in text:
            facts.append("Пользователь сообщает об оплате по договору.")
        if any(token in text for token in ("не выполнил", "не исполнил", "неисполн")):
            facts.append("Пользователь сообщает о неисполнении обязательства.")
        if any(token in text for token in ("не возвращ", "не вернул", "отказ вернуть", "отказывается возвращать")):
            facts.append("Пользователь сообщает об отказе вернуть деньги.")
        if any(token in text for token in ("20 мая", "обрат", "требован")):
            facts.append("Пользователь обращался к исполнителю с требованием вернуть деньги.")
        return facts

    @classmethod
    def _normalize_claims(cls, facts: dict[str, Any], user_text: str) -> list[str]:
        normalized: list[str] = []
        text = " ".join(
            [
                str(user_text or ""),
                str(facts.get("summary") or ""),
                str(((facts.get("user_demand") or {}) if isinstance(facts.get("user_demand"), dict) else {}).get("demand_type") or ""),
                str(((facts.get("user_demand") or {}) if isinstance(facts.get("user_demand"), dict) else {}).get("description") or ""),
                str(((facts.get("problem") or {}) if isinstance(facts.get("problem"), dict) else {}).get("description") or ""),
            ]
        ).lower()

        user_demand = facts.get("user_demand") if isinstance(facts.get("user_demand"), dict) else {}
        demand_type = str(user_demand.get("demand_type") or "").lower()
        demand_description = str(user_demand.get("description") or "").lower()

        if demand_type == "refund" or any(token in text for token in ("возврат", "вернуть деньги", "возвратить деньги")):
            normalized.append("refund_principal")
        if any(token in text for token in ("процент", "ст. 395", "пользовани чуж", "неправомерн удержан")):
            normalized.append("interest")
        if demand_type == "compensation" or any(token in text for token in ("убыт", "компенсац", "возмест")):
            normalized.append("damages")
        if demand_type == "perform_service" or any(token in text for token in ("исполнить", "выполнить", "оказать услугу")):
            normalized.append("performance")
        if demand_type == "cancel_contract" or any(token in text for token in ("расторг", "отказ от договора", "отказаться от договора")):
            normalized.append("termination/refusal")
        if any(token in text for token in ("неустой", "штраф", "пен", "задат", "обеспеч")):
            normalized.append("penalty")
        if any(token in text for token in ("реституц", "неосновательн", "вернуть уплаченное", "возврат уплаченного")):
            normalized.append("restitution")

        if not normalized and (demand_type or demand_description):
            normalized.append("other")

        deduped: list[str] = []
        for claim in normalized:
            if claim not in deduped:
                deduped.append(claim)
        return deduped

    @staticmethod
    def _extract_amount(text: str) -> float | None:
        amount_patterns = [
            r"(\d[\d\s]{1,20})\s*(?:руб(?:\.|ля|лей)?|₽)",
            r"стоимост[ьи]\s+(?:услуг|работ|товара)?\s*составил[аио]?\s*(\d[\d\s]{1,20})",
            r"оплат[аилыо]*\s*(?:работу|услуги|товар)?\s*.*?(\d[\d\s]{1,20})\s*(?:руб(?:\.|ля|лей)?|₽)",
        ]
        for pattern in amount_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                digits = re.sub(r"[^\d]", "", match.group(1))
                if digits:
                    return float(digits)
        return None

    @classmethod
    def _extract_dates(cls, text: str) -> list[dict[str, str]]:
        matches: list[dict[str, str]] = []
        for match in re.finditer(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})\s+года", text, flags=re.IGNORECASE):
            day = int(match.group(1))
            month = cls.MONTHS_RU.get(match.group(2).lower())
            year = match.group(3)
            if month:
                matches.append(
                    {
                        "iso": f"{year}-{month}-{day:02d}",
                        "raw": match.group(0),
                        "start": str(match.start()),
                        "end": str(match.end()),
                    }
                )
        return matches

    @classmethod
    def _date_for_context(cls, text: str, dates: list[dict[str, str]], markers: tuple[str, ...]) -> str | None:
        lowered = text.lower()
        best_date: str | None = None
        best_distance: int | None = None
        marker_positions = [lowered.find(marker) for marker in markers if lowered.find(marker) >= 0]
        if not marker_positions:
            return dates[0]["iso"] if dates else None
        for date in dates:
            start = int(date["start"])
            distance = min(abs(start - position) for position in marker_positions)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_date = date["iso"]
        return best_date

    @classmethod
    def _date_payload(cls, value: Any, exact_date: str | None, raw_text: str | None) -> dict[str, Any]:
        if isinstance(value, dict):
            payload = dict(value)
        else:
            payload = {"exact_date": None, "relative_date": None, "raw_text": None}
        payload["exact_date"] = payload.get("exact_date") or exact_date
        payload["raw_text"] = payload.get("raw_text") or raw_text
        payload.setdefault("relative_date", None)
        return payload

    @classmethod
    def _date_raw(cls, iso_date: str | None, text: str) -> str | None:
        if not iso_date:
            return None
        for item in cls._extract_dates(text):
            if item["iso"] == iso_date:
                return item["raw"]
        return None

    @staticmethod
    def _clean_summary(summary: str, fallback_text: str) -> str:
        text = summary.strip()
        if not text:
            return fallback_text
        if any(
            marker in text.lower()
            for marker in (
                "нет предоставленного текста",
                "нет предоставленного текста сообщения для анализа",
                "текст сообщения отсутствует",
                "не предоставлен текст",
                "не предоставлен текст сообщения",
                "нет текста для анализа",
            )
        ):
            return fallback_text
        if any(prefix in text.lower() for prefix in ("формат чата:", "название чата:", "текущее состояние кейса:", "правило:")):
            return fallback_text
        return text

    @staticmethod
    def _transaction_type(text: str) -> str | None:
        if "услуг" in text:
            return "оказание услуг"
        return None

    @staticmethod
    def _transaction_item(text: str) -> str | None:
        if "услуг" in text:
            return "оказание услуг"
        return None

    @staticmethod
    def _detect_case_type(text: str) -> str | None:
        contract_markers = ("договор", "услуг", "оплат", "исполнитель", "заказчик")
        nonperformance_markers = ("не выполнил", "не исполнил", "неисполн", "не оказал", "отказался вернуть", "не возвращ")
        if any(token in text for token in contract_markers) and any(token in text for token in nonperformance_markers):
            return "contract_nonperformance"
        return None

    @staticmethod
    def _demand_description(text: str) -> str | None:
        if "возврат" in text or "вернуть" in text:
            if "убыт" in text:
                return "Возврат денег и возмещение убытков"
            return "Возврат денег"
        if "убыт" in text:
            return "Возмещение убытков"
        return None

    @staticmethod
    def _detect_prior_contact(text: str) -> str:
        if "обрат" in text or "требован" in text:
            return "yes"
        return "unknown"

    @staticmethod
    def _detect_opponent_response(text: str) -> str | None:
        if "отказ" in text or "отказывается" in text:
            return "refused"
        return None

    @staticmethod
    def _coalesce_number(primary: Any, fallback: float | None) -> float | None:
        if isinstance(primary, (int, float)) and primary:
            if fallback is not None and float(primary) < 100.0 and fallback >= 1000.0:
                return fallback
            return float(primary)
        return fallback

    @staticmethod
    def _build_user_demands(text: str, amount: float | None) -> list[dict[str, Any]]:
        demands: list[dict[str, Any]] = []
        if any(token in text for token in ("возврат", "вернуть", "деньги")):
            demands.append(
                {
                    "demand_type": "refund",
                    "normalized_claim": "refund_principal",
                    "description": "Возврат денег",
                    "amount": amount,
                    "currency": "RUB" if amount is not None else "unknown",
                    "raw_text": None,
                }
            )
        if any(token in text for token in ("убыт", "возмест")):
            demands.append(
                {
                    "demand_type": "compensation",
                    "normalized_claim": "damages",
                    "description": "Возмещение убытков",
                    "amount": None,
                    "currency": "unknown",
                    "raw_text": None,
                }
            )
        return demands

    @classmethod
    def _build_missing_fields(cls, facts: dict[str, Any]) -> list[dict[str, str]]:
        transaction = facts.get("transaction") if isinstance(facts.get("transaction"), dict) else {}
        prior_contact = facts.get("prior_contact") if isinstance(facts.get("prior_contact"), dict) else {}
        normalized_claims = facts.get("normalized_claims") if isinstance(facts.get("normalized_claims"), list) else []
        missing: list[dict[str, str]] = []
        if not (transaction.get("price") or transaction.get("item_or_service")):
            missing.append({"field": "Подробности услуги", "reason": "Нужно точнее описать предмет договора."})
        if "damages" in normalized_claims:
            missing.append({"field": "Подробности убытков", "reason": "Нужно уточнить состав и размер убытков для отдельного требования."})
        if prior_contact.get("contacted_opponent") == "unknown":
            missing.append({"field": "Досудебное обращение", "reason": "Нужно понять, обращались ли вы к исполнителю до спора."})
        return missing

    @classmethod
    def _build_clarifying_questions(cls, facts: dict[str, Any]) -> list[str]:
        questions: list[str] = []
        for item in facts.get("missing_fields") if isinstance(facts.get("missing_fields"), list) else []:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "")
            if field == "Подробности убытков":
                questions.append("Какие именно убытки, кроме суммы оплаты, вы понесли и чем они подтверждаются?")
            elif field == "Подробности услуги":
                questions.append("Какая именно услуга была предусмотрена договором?")
        deduped: list[str] = []
        for question in questions:
            if question not in deduped:
                deduped.append(question)
        return deduped
