from app.services.article_semantic_analyzer import ArticleSemanticAnalyzer
from app.services.claim_entailment_checker import ClaimEntailmentChecker
from app.services.law_reranker import LawReranker
from app.services.legal_context_validator import LegalContextValidator


class StubPromptLoader:
    def load(self, name: str) -> str:
        return "{{CANDIDATE_ARTICLES}}"


class EmptyLLM:
    def complete_json(self, prompt: str, model: str) -> dict:
        return {"items": []}


class OkLLM:
    def complete_json(self, prompt: str, model: str) -> dict:
        return {"status": "ok", "confidence": 1.0, "has_direct_basis": True, "missing_facts": [], "warnings": []}


def test_similar_refund_words_do_not_cover_without_article_trigger_facts() -> None:
    analyzer = ArticleSemanticAnalyzer()
    checker = ClaimEntailmentChecker()
    article = {
        "id": "invalidity",
        "article_title": "Последствия недействительности сделки",
        "article_text": "Недействительная сделка недействительна. Каждая из сторон обязана возвратить другой все полученное по сделке.",
    }
    article["semantic_analysis"] = analyzer.analyze(article)

    coverage = checker.build_coverage(["refund_principal"], [article], {"summary": "Исполнитель не вернул оплату по договору услуг"}, "вернуть деньги")

    assert coverage["claims"]["refund_principal"]["covered"] is False
    assert coverage["claims"]["refund_principal"]["coverage_type"] == "blocked_by_missing_facts"
    assert coverage["missing_claims"] == ["refund_principal"]


def test_interest_article_about_delay_of_return_does_not_cover_refund_principal() -> None:
    analyzer = ArticleSemanticAnalyzer()
    checker = ClaimEntailmentChecker()
    article = {
        "id": "money_interest",
        "article_title": "Ответственность за неисполнение денежного обязательства",
        "article_text": "В случаях неправомерного удержания денежных средств, уклонения от их возврата, иной просрочки в их уплате подлежат уплате проценты на сумму долга.",
    }
    article["semantic_analysis"] = analyzer.analyze(article)

    effect_types = {effect["effect_type"] for effect in article["semantic_analysis"]["legal_effects"]}
    coverage = checker.build_coverage(["refund_principal"], [article], {"summary": "Исполнитель не вернул оплату по договору услуг"}, "вернуть деньги")

    assert "return_principal" not in effect_types
    assert "return_received" not in effect_types
    assert coverage["claims"]["refund_principal"]["covered"] is False
    assert coverage["claims"]["refund_principal"]["coverage_type"] in {"missing", "blocked_by_missing_facts"}


def test_conditional_article_does_not_cover_without_trigger_conditions() -> None:
    analyzer = ArticleSemanticAnalyzer()
    checker = ClaimEntailmentChecker()
    article = {
        "id": "termination",
        "article_title": "Последствия расторжения договора",
        "article_text": "При расторжении договора стороны вправе требовать возврата исполненного при наличии условий, установленных законом.",
    }
    article["semantic_analysis"] = analyzer.analyze(article)

    coverage = checker.build_coverage(["refund_principal"], [article], {"summary": "Исполнитель не выполнил работу"}, "не вернул деньги")

    assert coverage["claims"]["refund_principal"]["covered"] is False
    assert "termination_or_refusal" in coverage["claims"]["refund_principal"]["blocked_by_missing_facts"]


def test_guarantee_specific_damages_article_does_not_cover_generic_contract_damages() -> None:
    analyzer = ArticleSemanticAnalyzer()
    checker = ClaimEntailmentChecker()
    article = {
        "id": "guarantee_damages",
        "article_title": "Ответственность бенефициара",
        "article_text": "Бенефициар обязан возместить гаранту или принципалу убытки, которые причинены вследствие необоснованного требования по независимой гарантии.",
    }
    article["semantic_analysis"] = analyzer.analyze(article)

    coverage = checker.build_coverage(["damages"], [article], {"summary": "Исполнитель не выполнил договор услуг"}, "хочу взыскать убытки")

    assert coverage["claims"]["damages"]["covered"] is False
    assert "guarantee_context" in coverage["claims"]["damages"]["blocked_by_missing_facts"]


def test_special_refusal_ground_does_not_directly_cover_generic_damages() -> None:
    analyzer = ArticleSemanticAnalyzer()
    checker = ClaimEntailmentChecker()
    article = {
        "id": "license_refusal",
        "article_title": "Отказ от договора",
        "article_text": "В случае отсутствия у одной из сторон лицензии, необходимой для исполнения обязательства, другая сторона вправе отказаться от договора и потребовать возмещения убытков.",
    }
    article["semantic_analysis"] = analyzer.analyze(article)

    coverage = checker.build_coverage(["damages"], [article], {"summary": "Исполнитель не выполнил договор услуг"}, "хочу взыскать убытки")

    assert coverage["claims"]["damages"]["covered"] is False
    assert "regulatory_license_missing" in coverage["claims"]["damages"]["blocked_by_missing_facts"]


def test_supporting_article_does_not_close_primary_claim_without_direct_basis() -> None:
    service = LawReranker(EmptyLLM(), StubPromptLoader())
    service.rerank(
        "Требую вернуть деньги",
        {"summary": "Есть договор и оплата", "normalized_claims": ["refund_principal"]},
        {"primary_area": "civil"},
        [
            {
                "id": "performance",
                "act_name": "ГК РФ",
                "article_title": "Исполнение обязательств",
                "article_text": "Обязательства должны исполняться надлежащим образом.",
                "combined_score": 0.9,
            }
        ],
    )

    assert service.last_trace["coverage"]["claims"]["refund_principal"]["covered"] is False
    assert service.last_trace["coverage"]["missing_claims"] == ["refund_principal"]


def test_validator_does_not_override_missing_coverage_map_with_llm() -> None:
    service = LegalContextValidator(OkLLM(), StubPromptLoader())
    article = {
        "id": "performance",
        "coverage": [
            {
                "claim": "refund_principal",
                "coverage_type": "supporting",
                "counts_as_covered": False,
                "missing_facts": [],
            }
        ],
    }

    result = service.validate("вернуть деньги", {"normalized_claims": ["refund_principal"]}, {"primary_area": "civil"}, [article])

    assert result["status"] != "ok"
    assert result["missing_claims"] == ["refund_principal"]


def test_partial_coverage_is_not_reported_as_fully_justified() -> None:
    service = LegalContextValidator(OkLLM(), StubPromptLoader())
    article = {
        "id": "damages",
        "coverage": [
            {
                "claim": "damages",
                "coverage_type": "direct",
                "counts_as_covered": True,
                "missing_facts": [],
            }
        ],
    }

    result = service.validate(
        "вернуть деньги и убытки",
        {"normalized_claims": ["refund_principal", "damages"]},
        {"primary_area": "civil"},
        [article],
    )

    assert result["status"] == "partial"
    assert result["covered_claims"] == ["damages"]
    assert result["missing_claims"] == ["refund_principal"]


def test_repeated_temperature_zero_run_produces_same_coverage_map() -> None:
    service = LawReranker(EmptyLLM(), StubPromptLoader())
    facts = {
        "summary": "Исполнитель не выполнил работу, клиент требует убытки",
        "problem": {"type": "nonperformance"},
        "normalized_claims": ["damages"],
    }
    articles = [
        {
            "id": "damages",
            "act_name": "ГК РФ",
            "article_title": "Обязанность должника возместить убытки",
            "article_text": "Должник обязан возместить кредитору убытки, причиненные неисполнением обязательства.",
            "combined_score": 0.8,
        }
    ]

    service.rerank("требую убытки", facts, {"primary_area": "civil"}, articles)
    first = service.last_trace["coverage"]["coverage_map"]
    service.rerank("требую убытки", facts, {"primary_area": "civil"}, articles)
    second = service.last_trace["coverage"]["coverage_map"]

    assert first == second
