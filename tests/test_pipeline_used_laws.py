from app.services.pipeline import ClaimPipeline


def test_extract_used_laws_normalizes_string_list_against_legal_context() -> None:
    legal_context = [
        {
            "id": "law-393",
            "act_name": "ГК РФ",
            "act_type": "code",
            "article_number": "393",
            "article_title": "Обязанность должника возместить убытки",
            "article_text": "Должник обязан возместить убытки...",
            "source_file": "gk_393.txt",
            "relevance_score": 0.8,
        },
        {
            "id": "law-15",
            "act_name": "ГК РФ",
            "act_type": "code",
            "article_number": "15",
            "article_title": "Возмещение убытков",
            "article_text": "Лицо, право которого нарушено...",
            "source_file": "gk_15.txt",
            "relevance_score": 0.7,
        },
    ]
    claim_json = {"used_laws": ["ГК РФ, ст. 393", "ГК РФ, ст. 15"]}

    result = ClaimPipeline._extract_used_laws(claim_json, legal_context)

    assert [item["id"] for item in result] == ["law-393", "law-15"]
    assert result[0]["article_title"] == "Обязанность должника возместить убытки"
    assert result[1]["article_number"] == "15"


def test_extract_used_laws_builds_minimal_dict_for_parsed_string_without_context_match() -> None:
    claim_json = {"used_laws": ["ГК РФ, ст. 314"]}

    result = ClaimPipeline._extract_used_laws(claim_json, [])

    assert result == [
        {
            "id": "parsed-law-0",
            "act_name": "ГК РФ",
            "article_number": "314",
        }
    ]


def test_contract_diagnostic_logs_missing_expected_articles(monkeypatch) -> None:
    events = []

    def capture(event: str, **payload):
        events.append((event, payload))

    pipeline = ClaimPipeline.__new__(ClaimPipeline)
    pipeline.settings = type("Settings", (), {"log_rag_trace": True, "log_rag_trace_full": False})()
    pipeline.law_query_builder = type(
        "QueryBuilder",
        (),
        {"last_trace": {"detected_scenario": "contract_services_nonperformance_refund"}},
    )()

    monkeypatch.setattr("app.services.pipeline.log_json", capture)

    pipeline._log_expected_articles_diagnostic(
        request_id="req-1",
        run_id="run-1",
        user_text="Исполнитель не исполнил договор услуг и не вернул деньги",
        facts={"summary": "Спор о неисполнении договора"},
        legal_area={"primary_area": "civil"},
        legal_context=[
            {"article_number": "15"},
            {"article_number": "393"},
            {"article_number": "405"},
        ],
    )

    assert events
    event, payload = events[0]
    assert event == "legal_rag.diagnostic.expected_articles_missing"
    assert payload["present_articles"] == ["15", "393", "405"]
    assert payload["missing_articles"] == ["309", "314", "450", "453"]


def test_compact_trace_payload_drops_heavy_article_fields() -> None:
    pipeline = ClaimPipeline.__new__(ClaimPipeline)
    pipeline.settings = type("Settings", (), {"log_rag_trace_full": False})()

    payload = {
        "candidate_articles": [
            {
                "id": "law-309",
                "act_name": "ГК РФ",
                "article_number": "309",
                "article_title": "Общие положения",
                "article_text": "Очень длинный текст статьи",
                "article_parts": [{"part": "1", "text": "..." * 20}],
                "keyword_score": 0.9,
                "vector_score": 0.8,
                "combined_score": 0.85,
            }
        ],
        "facts": {
            "summary": "Спор по договору услуг",
            "preliminary_case_type": "service_delay",
            "parties": {"claimant_role": "customer", "opponent_role": "service_provider"},
            "transaction": {"item_or_service": "услуги", "price_amount": 120000, "date": "2026-05-10"},
            "problem": {"type": "nonperformance", "description": "Подробное описание"},
            "demand": {"type": "refund"},
            "known_facts": ["договор", "оплата"],
            "missing_fields": ["term"],
            "clarifying_questions": ["Когда истек срок?"],
        },
        "claim_text": "Полный текст претензии",
    }

    compact = pipeline._compact_trace_payload(payload)

    article = compact["candidate_articles"][0]
    assert "article_text" not in article
    assert "article_parts" not in article
    assert "claim_text" not in compact
    assert compact["facts"]["summary"] == "Спор по договору услуг"
    assert compact["facts"]["transaction"]["price"] == 120000


def test_trace_summary_uses_compact_articles() -> None:
    articles = ClaimPipeline._summary_articles_for_step(
        "hybrid_law_retrieval",
        {
            "merged_candidates": [
                {
                    "article_number": "393",
                    "title": "Возмещение убытков",
                    "source": "both",
                    "combined_score": 0.91,
                    "applicability": "direct",
                    "article_text": "Не должно попасть в summary",
                }
            ]
        },
    )

    assert articles == [
        {
            "article_number": "393",
            "title": "Возмещение убытков",
            "source": "both",
            "score": 0.91,
            "applicability": "direct",
        }
    ]


def test_log_reranker_diagnostics_emits_coverage_and_dropped(monkeypatch) -> None:
    events = []

    def capture(event: str, **payload):
        events.append((event, payload))

    pipeline = ClaimPipeline.__new__(ClaimPipeline)
    pipeline.settings = type("Settings", (), {"log_rag_trace": True, "log_rag_trace_full": False})()
    monkeypatch.setattr("app.services.pipeline.log_json", capture)

    pipeline._log_reranker_diagnostics(
        request_id="req-1",
        run_id="run-1",
        duration_ms=12.5,
        trace={
            "coverage": {
                "detected_legal_roles": ["obligation_basis", "liability"],
                "selected_article_per_role": {
                    "liability": {
                        "id": "law-393",
                        "act_name": "ГК РФ",
                        "article_number": "393",
                        "article_title": "Обязанность должника возместить убытки",
                        "relevance_score": 0.88,
                    }
                },
                "missing_roles": ["performance_terms"],
            },
            "role_corrections": [
                {
                    "id": "law-395",
                    "act_name": "ГК РФ",
                    "article_number": "395",
                    "from_role": "refund_or_restitution",
                    "to_role": "monetary_obligation_interest",
                    "reason": "registry_disallowed_role",
                }
            ],
            "dropped_relevant_candidates": [
                {
                    "id": "law-333",
                    "act_name": "ГК РФ",
                    "article_number": "333",
                    "article_title": "Уменьшение неустойки",
                    "relevance_score": 0.81,
                    "reason": "conditional_role_not_confirmed",
                }
            ],
        },
    )

    assert [event for event, _ in events] == [
        "legal_rag.coverage.roles",
        "legal_rag.role_corrected",
        "legal_rag.reranker.dropped_relevant_candidates",
    ]
    assert events[1][1]["to_role"] == "monetary_obligation_interest"
    assert events[2][1]["articles"][0]["reason"] == "conditional_role_not_confirmed"
