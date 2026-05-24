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
