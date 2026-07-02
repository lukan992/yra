from app.services.fact_extractor import FactExtractor


def test_fact_extractor_postprocess_adds_normalized_claims() -> None:
    facts = {
        "summary": "Исполнитель не выполнил договор и не вернул деньги, пользователь требует возврат и убытки",
        "user_demand": {
            "demand_type": "refund",
            "description": "Возврат денег и возмещение убытков",
        },
    }

    result = FactExtractor._postprocess_facts(facts, "Требую возврат денег и убытки")

    assert result["normalized_claims"] == ["refund_principal", "damages"]


def test_fact_extractor_postprocess_restores_minimum_shape_when_llm_returns_only_claims() -> None:
    result = FactExtractor._postprocess_facts(
        {"normalized_claims": ["refund_principal", "damages"]},
        "Я заключил договор на оказание услуг, оплатил работу, но исполнитель не выполнил обязательство и деньги не возвращает",
    )

    assert result["normalized_claims"] == ["refund_principal", "damages"]
    assert result["summary"]
    assert isinstance(result["transaction"], dict)
    assert result["transaction"]["type"] == "оказание услуг"
    assert isinstance(result["known_facts"], list) and result["known_facts"]
    assert isinstance(result["missing_fields"], list) and result["missing_fields"]
    assert isinstance(result["clarifying_questions"], list) and result["clarifying_questions"]


def test_fact_extractor_ignores_chat_wrapper_and_extracts_service_contract_facts() -> None:
    wrapped_input = (
        "Формат чата: общий юридический чат "
        "Название чата: Новый чат "
        "Текущее состояние кейса: это начало диалога. "
        "Правило: собери факты. "
        "История диалога: user: "
        "Я заключил договор на оказание услуг, оплатил работу, но исполнитель не выполнил обязательство в срок "
        "и деньги не возвращает. Могу ли я требовать возврат денег и возмещение убытков? "
        "Стоимость услуг составила 120000 рублей. "
        "Договор был заключен и оплата произведена 10 мая 2026 года. "
        "20 мая 2026 года я обратился к исполнителю с требованием вернуть деньги, но он отказался."
    )

    result = FactExtractor._postprocess_facts({}, wrapped_input)

    assert "Формат чата" not in result["summary"]
    assert "Название чата" not in result["summary"]
    assert result["transaction"]["price"] == 120000.0
    assert result["transaction"]["type"] == "оказание услуг"
    assert result["transaction"]["item_or_service"] == "оказание услуг"
    assert result["transaction"]["contract_date"] == "2026-05-10"
    assert result["transaction"]["purchase_or_order_date"]["exact_date"] == "2026-05-10"
    assert result["transaction"]["payment_date"]["exact_date"] == "2026-05-10"
    assert result["prior_contact"]["contact_date"]["exact_date"] == "2026-05-20"
    assert result["prior_contact"]["opponent_response"] == "refused"
    assert result["parties_roles"]["opponent_role"] == "service_provider"
    assert result["parties"]["opponent_role"] == "service_provider"
    assert result["preliminary_case_type"] == "contract_nonperformance"
    assert result["problem_type"] == "nonperformance_or_delay"
    assert result["demand_type"] == "refund"
    assert result["normalized_claims"] == ["refund_principal", "damages"]


def test_fact_extractor_does_not_ask_duplicate_known_fact_questions() -> None:
    text = (
        "Я заключил договор на оказание услуг, оплатил работу, но исполнитель не выполнил обязательство в срок "
        "и деньги не возвращает. Стоимость услуг составила 120000 рублей. "
        "Договор был заключен и оплата произведена 10 мая 2026 года. "
        "20 мая 2026 года я обратился к исполнителю с требованием вернуть деньги, но он отказался. "
        "Хочу возврат денег и возмещение убытков."
    )

    result = FactExtractor._postprocess_facts({}, text)

    assert result["clarifying_questions"] == [
        "Какие именно убытки, кроме суммы оплаты, вы понесли и чем они подтверждаются?"
    ]


def test_fact_extractor_restores_case_type_and_summary_from_wrapper_when_llm_returns_sentinel() -> None:
    wrapped_input = (
        "Формат чата: общий юридический чат\n"
        "История диалога:\n"
        "assistant: Уточните обстоятельства.\n"
        "user: Я заключил договор на оказание услуг, оплатил работу, но исполнитель не выполнил обязательство "
        "и деньги не возвращает. Требую возврат оплаты и убытки."
    )

    result = FactExtractor._postprocess_facts(
        {
            "summary": "Нет предоставленного текста сообщения для анализа.",
            "preliminary_case_type": "unknown",
        },
        wrapped_input,
    )

    assert result["preliminary_case_type"] == "contract_nonperformance"
    assert result["summary"] != "Нет предоставленного текста сообщения для анализа."
    assert "Формат чата" not in result["summary"]
    assert "assistant:" not in result["summary"]
    assert "договор на оказание услуг" in result["summary"]
