# PRD: MVP юридического RAG-сервиса для генерации досудебной претензии

## 1. Назначение документа

Этот документ описывает MVP внутреннего web-прототипа юридической системы, которая принимает свободное описание проблемы пользователя, анализирует ситуацию с помощью LLM, ищет применимые нормы Закона РФ «О защите прав потребителей» в локальной базе знаний и возвращает структурированный JSON-результат.

На этапе MVP система предназначена не для публичного использования, а для проверки качества анализа и генерации досудебной претензии.

---

## 2. Цель MVP

### Основная цель

Проверить качество генерации юридически обоснованной досудебной претензии на основе свободного описания проблемы пользователя и найденных норм права.

### Что проверяется в MVP

1. Способность системы извлекать юридически значимые факты из свободного текста.
2. Способность определить, применим ли Закон РФ «О защите прав потребителей».
3. Способность находить релевантные статьи ЗоЗПП из базы знаний.
4. Способность определить, можно ли сформировать досудебную претензию.
5. Способность заполнить итоговый JSON на основе имеющихся данных.
6. Способность выявить недостающие обязательные поля и сформировать уточняющие вопросы.
7. Способность не выдумывать статьи, даты, суммы и иные факты.
8. Способность валидировать результат отдельной LLM-моделью через LiteLLM.

---

## 3. Область MVP

### Включено в MVP

1. Web backend без frontend.
2. Прием свободного текста проблемы пользователя.
3. Анализ только по российской юрисдикции.
4. Работа только с Законом РФ «О защите прав потребителей».
5. Поддержка всех пользовательских кейсов, но с маршрутизацией неподходящих ситуаций в статус `route_to_lawyer`.
6. Учет технически сложных товаров.
7. Поиск релевантных статей ЗоЗПП.
8. Генерация итогового JSON.
9. Сохранение обращений, промежуточных результатов и финального JSON в БД.
10. История обращений пользователя без авторизации.
11. Интеграция LLM через LiteLLM.
12. Отдельная LLM-модель для валидации.
13. Retry, timeout и fallback-модель для LLM-вызовов.
14. Логирование через Loguru.
15. Docker Compose для локального запуска.
16. Заглушка для будущего модуля загрузки документов.
17. Хранение промптов в папке `prompts/` в формате `.md`.

### Не включено в MVP

1. Frontend.
2. Авторизация пользователей.
3. Админка.
4. DOCX/PDF-экспорт.
5. Подбор юриста.
6. Юридическая проверка человеком.
7. Готовая БД законов на старте.
8. Шаблоны претензий.
9. Обработка загруженных документов.
10. Маскирование персональных данных в логах.
11. Дисклеймер «не является юридической консультацией».
12. Golden tests и фиксированный набор тестов на первом этапе.

---

## 4. Основной сценарий работы

```text
Пользователь вводит свободное описание проблемы
        ↓
Система сохраняет обращение
        ↓
LLM извлекает факты
        ↓
LLM классифицирует ситуацию в рамках ЗоЗПП
        ↓
Система ищет релевантные статьи ЗоЗПП
        ↓
Если статьи не найдены → ошибка с объяснением
        ↓
LLM оценивает возможность досудебной претензии
        ↓
Если данных не хватает → JSON + уточняющие вопросы
        ↓
Если претензия применима → LLM формирует JSON претензии
        ↓
Validator LLM проверяет результат
        ↓
Если есть критические ошибки → статус validation_failed
        ↓
Если ошибок нет → финальный JSON сохраняется и возвращается пользователю
```

---

## 5. Архитектура MVP

### Тип приложения

Web backend без frontend.

Рекомендуемый стек:

```text
FastAPI
PostgreSQL
SQLAlchemy / SQLModel
Alembic
LiteLLM client
Loguru
Docker Compose
Pydantic v2
```

### Основные модули

```text
app/
  main.py
  api/
    routes.py
  core/
    config.py
    logging.py
  db/
    models.py
    session.py
    migrations/
  services/
    legal_pipeline.py
    llm_client.py
    law_retriever.py
    fact_extractor.py
    case_classifier.py
    claim_evaluator.py
    claim_generator.py
    result_validator.py
    document_stub.py
  schemas/
    requests.py
    responses.py
    pipeline.py
  prompts/
    fact_extractor.md
    case_classifier.md
    claim_evaluator.md
    claim_generator.md
    result_validator.md
  scripts/
    parse_law_txt.py
```

---

## 6. Подключение LLM через LiteLLM

LLM подключается только через LiteLLM.

### `.env.example`

```env
# App
APP_ENV=local
APP_HOST=0.0.0.0
APP_PORT=8000

# Database
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=legal_mvp
POSTGRES_USER=legal_mvp
POSTGRES_PASSWORD=legal_mvp
DATABASE_URL=postgresql+psycopg://legal_mvp:legal_mvp@postgres:5432/legal_mvp

# LiteLLM
LITELLM_BASE_URL=
LITELLM_API_KEY=

# Main model
LITELLM_MAIN_MODEL=

# Validator model
LITELLM_VALIDATOR_MODEL=

# Optional fallback model
LITELLM_FALLBACK_MODEL=

# LLM settings
LLM_TIMEOUT_SECONDS=120
LLM_MAX_RETRIES=2
LLM_TEMPERATURE=0.1
LLM_VALIDATOR_TEMPERATURE=0.0

# Logging
LOG_LEVEL=INFO
LOG_LLM_PROMPTS=true
LOG_LLM_RESPONSES=true
```

### Требования к LLM-клиенту

1. Все LLM-вызовы проходят через единый сервис `llm_client.py`.
2. Поддерживаются разные модели:
   - `main_model` — анализ, классификация, генерация;
   - `validator_model` — проверка результата;
   - `fallback_model` — резервная модель при ошибке основной.
3. Для каждого вызова логируются:
   - имя промпта;
   - модель;
   - входной JSON;
   - полный prompt;
   - raw response;
   - parsed response;
   - время выполнения;
   - ошибка, если она произошла.
4. Логирование реализуется через Loguru.
5. При ошибке LLM-вызова система выполняет retry.
6. После исчерпания retry используется fallback-модель, если она настроена.
7. Если fallback не настроен или тоже завершился ошибкой, возвращается статус `llm_error`.

---

## 7. Источник законов и RAG

### Текущее состояние

На старте MVP нет готовой БД законов.

Есть `.txt`-файлы с текстом Закона РФ «О защите прав потребителей», которые позднее должны быть спарсены и загружены в PostgreSQL.

### Целевое хранение в PostgreSQL

В БД должны храниться полные тексты статей.

Рекомендуемая таблица:

```sql
CREATE TABLE law_articles (
    id UUID PRIMARY KEY,
    law_code TEXT NOT NULL,
    law_title TEXT NOT NULL,
    article_number TEXT NOT NULL,
    article_title TEXT,
    article_text TEXT NOT NULL,
    source_file TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);
```

Для MVP `law_code` всегда:

```text
consumer_protection_law
```

### Рекомендуемый поиск по законам

Для MVP лучше использовать гибридный подход, но с возможностью начать с простого полнотекстового поиска.

#### Этап 1: PostgreSQL full-text search

Использовать `tsvector` / `tsquery` для русского языка, если доступна конфигурация `russian`.

Плюсы:

1. Просто реализовать.
2. Не требует отдельного vector-хранилища.
3. Хорошо работает по точным юридическим словам: «недостаток», «возврат», «срок», «неустойка», «потребитель», «продавец».

#### Этап 2: vector search через pgvector

Добавить embeddings для статей или частей статей.

Плюсы:

1. Лучше работает с переформулированными пользовательскими проблемами.
2. Помогает находить статьи, даже если пользователь описывает проблему бытовым языком.

#### Рекомендуемая стратегия для MVP

```text
1. На первом этапе реализовать PostgreSQL full-text search.
2. Абстрагировать поиск через интерфейс LawRetriever.
3. Позже добавить pgvector без изменения pipeline.
4. Не использовать ручное сопоставление case_type → articles как основной механизм.
```

### Почему не использовать только case_type → articles

Ручное сопоставление быстрее для прототипа, но плохо масштабируется и не проверяет реальный RAG-пайплайн. Так как цель MVP — проверить качество генерации на основе найденных норм, поиск должен быть настоящим, а не полностью захардкоженным.

---

## 8. Pipeline по шагам

## 8.1. Шаг 1 — прием обращения

### Endpoint

```http
POST /api/v1/claims/analyze
```

### Request

```json
{
  "user_text": "Купил телефон, через неделю он перестал включаться. Магазин отказывается возвращать деньги."
}
```

### Действия системы

1. Проверить, что `user_text` не пустой.
2. Создать запись обращения в БД.
3. Запустить pipeline.

---

## 8.2. Шаг 2 — извлечение фактов

### Модуль

```text
FactExtractor
```

### Prompt file

```text
prompts/fact_extractor.md
```

### Задача

Извлечь из свободного текста юридически значимые факты.

### Выход

```json
{
  "problem_summary": "Покупатель приобрел телефон, который через неделю перестал включаться. Продавец отказался вернуть деньги.",
  "parties": {
    "applicant_role": "consumer",
    "opponent_role": "seller",
    "opponent_name": null
  },
  "transaction": {
    "type": "purchase",
    "item_or_service": "телефон",
    "purchase_date": null,
    "relative_purchase_time": "примерно неделю назад",
    "price": null,
    "is_technical_complex_goods": "unknown"
  },
  "problem": {
    "type": "defective_goods",
    "description": "Телефон перестал включаться.",
    "problem_date": null
  },
  "user_demand": {
    "type": "refund",
    "description": "Пользователь хочет вернуть деньги."
  },
  "prior_contact": {
    "contacted_opponent": true,
    "opponent_response": "Продавец отказался вернуть деньги."
  },
  "documents": {
    "receipt": "unknown",
    "contract": "unknown",
    "warranty_card": "unknown",
    "photos_or_video": "unknown",
    "correspondence": "unknown"
  },
  "missing_fields": [
    "ФИО заявителя",
    "адрес заявителя",
    "наименование продавца",
    "адрес продавца",
    "дата покупки",
    "стоимость товара",
    "наличие чека"
  ]
}
```

### Правила

1. Не придумывать даты.
2. Не придумывать суммы.
3. Не придумывать имена и адреса.
4. Если факт не указан, ставить `null` или `unknown`.
5. Относительные даты хранить отдельно от точных дат.

---

## 8.3. Шаг 3 — классификация кейса

### Модуль

```text
CaseClassifier
```

### Prompt file

```text
prompts/case_classifier.md
```

### Задача

Определить, относится ли проблема к сфере Закона РФ «О защите прав потребителей», и классифицировать тип ситуации.

### Важное требование

Система должна принимать любые кейсы пользователя, но если ситуация не относится к ЗоЗПП или является высокорисковой, она должна вернуть `route_to_lawyer`, а не пытаться сгенерировать претензию.

### Возможные `case_type`

```text
consumer_defective_goods
consumer_refund_goods
consumer_bad_service
consumer_service_delay
consumer_delivery_delay
consumer_repair_warranty
consumer_price_mismatch
consumer_subscription_cancel
consumer_marketplace_dispute
consumer_technical_complex_goods
not_consumer_case
high_risk_case
unknown
```

### Отдельная обработка технически сложных товаров

Если товар потенциально технически сложный, система должна выставить:

```json
{
  "is_technical_complex_goods": "yes",
  "technical_complexity_reasoning": "Пользователь указал телефон. Такой товар может относиться к технически сложным товарам. Нужно учитывать специальные правила возврата."
}
```

Если уверенности нет:

```json
{
  "is_technical_complex_goods": "unknown",
  "technical_complexity_reasoning": "Недостаточно данных, чтобы определить категорию товара."
}
```

---

## 8.4. Шаг 4 — проверка применимости ЗоЗПП

### Модуль

```text
ConsumerLawApplicabilityChecker
```

### Задача

Проверить признаки потребительского спора.

### Условия применимости

1. Заявитель — физическое лицо или вероятный потребитель.
2. Товар/услуга приобретались для личных, семейных, домашних или иных нужд, не связанных с предпринимательской деятельностью.
3. Вторая сторона — продавец, исполнитель, изготовитель, агрегатор, организация или ИП.
4. Спор связан с товаром, работой, услугой, доставкой, качеством, сроками, возвратом или оплатой.

### Выход

```json
{
  "consumer_law_applicability": {
    "is_applicable": true,
    "confidence": "medium",
    "matched_conditions": [
      "есть покупка товара",
      "есть недостаток товара",
      "есть отказ продавца вернуть деньги"
    ],
    "missing_or_uncertain_conditions": [
      "не указано, покупался ли товар для личных нужд",
      "не указано, является ли продавец организацией или ИП"
    ]
  }
}
```

---

## 8.5. Шаг 5 — поиск релевантных норм

### Модуль

```text
LawRetriever
```

### Задача

Найти в БД релевантные статьи ЗоЗПП.

### Вход

```json
{
  "problem_summary": "...",
  "case_type": "consumer_defective_goods",
  "facts": {...},
  "user_demand": {...}
}
```

### Выход

```json
{
  "legal_context": [
    {
      "article_id": "uuid",
      "law_code": "consumer_protection_law",
      "law_title": "Закон РФ «О защите прав потребителей»",
      "article_number": "18",
      "article_title": "Права потребителя при обнаружении в товаре недостатков",
      "article_text": "...",
      "relevance_score": 0.92
    }
  ]
}
```

### Если статьи не найдены

Система должна остановить генерацию и вернуть ошибку:

```json
{
  "status": "legal_context_not_found",
  "error": {
    "code": "LEGAL_CONTEXT_NOT_FOUND",
    "message": "Не удалось найти релевантные статьи Закона РФ «О защите прав потребителей» для описанной ситуации.",
    "details": "Генерация претензии остановлена, чтобы избежать выдумывания правовых оснований."
  }
}
```

---

## 8.6. Шаг 6 — оценка применимости досудебной претензии

### Модуль

```text
ClaimEvaluator
```

### Prompt file

```text
prompts/claim_evaluator.md
```

### Задача

Оценить, можно ли подготовить досудебную претензию на основе фактов и найденных норм.

### Возможные статусы

```text
applicable
need_more_info
route_to_lawyer
legal_context_not_found
```

### Выход

```json
{
  "pretrial_claim_evaluation": {
    "status": "applicable",
    "confidence": "high",
    "reasoning": "Спор связан с товаром ненадлежащего качества и требованием возврата денег к продавцу. Досудебная претензия применима.",
    "critical_missing_fields": [],
    "non_critical_missing_fields": [
      "ФИО заявителя",
      "адрес заявителя",
      "наименование продавца",
      "стоимость товара"
    ],
    "recommended_action": "generate_claim_json"
  }
}
```

---

## 8.7. Шаг 7 — проверка недостающих обязательных полей

### Модуль

```text
RequiredFieldsChecker
```

### Задача

После заполнения фактов проверить, каких обязательных данных не хватает.

### Логика

1. Система заполняет JSON тем, что известно из пользовательского текста.
2. Затем проверяет обязательные поля.
3. Если обязательных полей не хватает, система возвращает `need_more_info` и список уточняющих вопросов.
4. При этом уже извлеченный JSON сохраняется.

### Минимальные обязательные поля для полноценной претензии

```text
applicant_name
opponent_name
claim_subject
violation_description
user_demand
```

### Поля, которые могут быть неизвестны на первом шаге

```text
applicant_address
opponent_address
exact_purchase_date
price
receipt_exists
warranty_card_exists
```

### Пример результата

```json
{
  "status": "need_more_info",
  "filled_data": {
    "claim_subject": "телефон",
    "violation_description": "телефон перестал включаться",
    "user_demand": "возврат денег"
  },
  "missing_required_fields": [
    "opponent_name",
    "applicant_name"
  ],
  "clarifying_questions": [
    "Как называется продавец или магазин?",
    "Укажите ваше ФИО для претензии."
  ]
}
```

---

## 8.8. Шаг 8 — генерация JSON претензии

### Модуль

```text
ClaimGenerator
```

### Prompt file

```text
prompts/claim_generator.md
```

### Важное ограничение

На этапе MVP шаблоны претензий не используются.

LLM генерирует структуру претензии внутри JSON на основе:

1. Извлеченных фактов.
2. Найденных статей.
3. Оценки применимости претензии.
4. Требования пользователя.

### Запрещено

1. Придумывать статьи.
2. Придумывать даты.
3. Придумывать суммы.
4. Придумывать данные сторон.
5. Добавлять требования, которых пользователь не заявлял.
6. Автоматически добавлять моральный вред, штраф, судебные расходы и неустойку, если пользователь этого не просил.
7. Использовать нормы, которых нет в `legal_context`.

### Выход

```json
{
  "claim_document": {
    "title": "Досудебная претензия о возврате денежных средств за товар ненадлежащего качества",
    "recipient": {
      "name": "[не указано]",
      "address": "[не указано]"
    },
    "applicant": {
      "name": "[не указано]",
      "address": "[не указано]",
      "phone": "[не указано]",
      "email": "[не указано]"
    },
    "facts_section": "Пользователь указал, что приобрел телефон, который примерно через неделю перестал включаться. Продавец отказался вернуть деньги.",
    "legal_basis": [
      {
        "article_number": "18",
        "article_title": "Права потребителя при обнаружении в товаре недостатков",
        "usage_reason": "Статья использована как правовое основание требований при обнаружении недостатков товара."
      }
    ],
    "demands": [
      "Вернуть денежные средства за товар."
    ],
    "attachments": [
      "Копия чека при наличии",
      "Копия гарантийного талона при наличии",
      "Копия переписки с продавцом при наличии"
    ],
    "text": "..."
  }
}
```

---

## 8.9. Шаг 9 — LLM-валидация результата

### Модуль

```text
ResultValidator
```

### Prompt file

```text
prompts/result_validator.md
```

### Модель

Используется отдельная модель из LiteLLM:

```env
LITELLM_VALIDATOR_MODEL=
```

### Задача

Проверить финальный JSON и текст претензии на критические ошибки.

### Критические ошибки

1. Выдуманная статья.
2. Ссылка на статью, которой нет в `legal_context`.
3. Не тот тип претензии.
4. Выдуманная дата.
5. Выдуманная сумма.
6. Выдуманные данные заявителя.
7. Выдуманные данные продавца.
8. Требования, которых пользователь не заявлял.
9. Противоречие между фактами и текстом претензии.
10. Генерация претензии при отсутствии релевантных норм.

### Выход validator-модели

```json
{
  "validation": {
    "is_valid": true,
    "severity": "none",
    "issues": [],
    "hallucinated_laws": [],
    "invented_facts": [],
    "wrong_claim_type": false,
    "final_recommendation": "approve"
  }
}
```

Если найдены ошибки:

```json
{
  "validation": {
    "is_valid": false,
    "severity": "critical",
    "issues": [
      "В претензии указана статья, которой нет в legal_context.",
      "В тексте появилась сумма, которой не было в исходном обращении."
    ],
    "hallucinated_laws": ["статья 31"],
    "invented_facts": ["сумма 50 000 рублей"],
    "wrong_claim_type": false,
    "final_recommendation": "reject"
  }
}
```

---

## 8.10. Шаг 10 — финальный ответ

### Успешный ответ

```json
{
  "request_id": "uuid",
  "status": "claim_generated",
  "jurisdiction": "RU",
  "law_scope": "consumer_protection_law",
  "case_type": "consumer_defective_goods",
  "summary": "Покупатель приобрел телефон, который через неделю перестал включаться. Продавец отказался вернуть деньги.",
  "decision": {
    "pretrial_claim_status": "applicable",
    "confidence": "high",
    "reasoning": "Досудебная претензия применима, поскольку спор связан с товаром ненадлежащего качества и требованием возврата денег к продавцу."
  },
  "used_laws": [
    {
      "article_id": "uuid",
      "law_title": "Закон РФ «О защите прав потребителей»",
      "article_number": "18",
      "article_title": "Права потребителя при обнаружении в товаре недостатков"
    }
  ],
  "missing_fields": [
    "ФИО заявителя",
    "наименование продавца",
    "стоимость товара"
  ],
  "clarifying_questions": [
    "Укажите ваше ФИО для претензии.",
    "Как называется продавец или магазин?",
    "Укажите стоимость товара."
  ],
  "claim_document": {
    "title": "Досудебная претензия о возврате денежных средств за товар ненадлежащего качества",
    "text": "..."
  },
  "validation": {
    "is_valid": true,
    "final_recommendation": "approve"
  }
}
```

### Ответ при нехватке данных

```json
{
  "request_id": "uuid",
  "status": "need_more_info",
  "filled_data": {...},
  "missing_required_fields": [
    "opponent_name",
    "applicant_name"
  ],
  "clarifying_questions": [
    "Как называется продавец или магазин?",
    "Укажите ваше ФИО для претензии."
  ]
}
```

### Ответ при отсутствии релевантных статей

```json
{
  "request_id": "uuid",
  "status": "legal_context_not_found",
  "error": {
    "code": "LEGAL_CONTEXT_NOT_FOUND",
    "message": "Не удалось найти релевантные статьи Закона РФ «О защите прав потребителей» для описанной ситуации.",
    "details": "Генерация претензии остановлена, чтобы избежать выдумывания правовых оснований."
  }
}
```

### Ответ при необходимости юриста

```json
{
  "request_id": "uuid",
  "status": "route_to_lawyer",
  "reason": "Ситуация не относится к типовым потребительским претензиям или требует индивидуального юридического анализа.",
  "recommended_action": "Обратиться к юристу. Подбор юриста на этапе MVP не реализован."
}
```

---

## 9. Работа с уточняющими вопросами

### Требование

Если данных не хватает, система не должна придумывать недостающие значения.

Она должна:

1. Заполнить JSON теми данными, которые есть.
2. Определить недостающие обязательные поля.
3. Сформировать список уточняющих вопросов.
4. Сохранить состояние обращения.
5. Позволить позже продолжить обращение после ответа пользователя.

### Endpoint для продолжения

```http
POST /api/v1/claims/{request_id}/continue
```

### Request

```json
{
  "answers": {
    "opponent_name": "ООО Ромашка",
    "applicant_name": "Иванов Иван Иванович",
    "price": "50000"
  }
}
```

### Действия

1. Загрузить сохраненное обращение.
2. Обновить факты.
3. Повторить проверку обязательных полей.
4. Если данных достаточно — продолжить генерацию.
5. Если нет — вернуть новые уточняющие вопросы.

---

## 10. Заглушка модуля документов

На этапе MVP загрузка документов не реализуется, но должен быть предусмотрен интерфейс.

### Модуль

```text
DocumentStubService
```

### Назначение

Подготовить место для будущей обработки:

1. чеков;
2. договоров;
3. гарантийных талонов;
4. переписок;
5. фотографий;
6. PDF/DOCX-файлов.

### В MVP

Модуль всегда возвращает:

```json
{
  "documents_enabled": false,
  "message": "Document processing is not implemented in MVP."
}
```

---

## 11. База данных

### Основные таблицы

#### `requests`

Хранит обращения пользователя.

```sql
CREATE TABLE requests (
    id UUID PRIMARY KEY,
    raw_user_text TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);
```

#### `pipeline_runs`

Хранит состояние pipeline.

```sql
CREATE TABLE pipeline_runs (
    id UUID PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES requests(id),
    step_name TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json JSONB,
    output_json JSONB,
    error_json JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
```

#### `claim_results`

Хранит финальный результат.

```sql
CREATE TABLE claim_results (
    id UUID PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES requests(id),
    final_status TEXT NOT NULL,
    final_json JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
```

#### `law_articles`

Хранит статьи закона.

```sql
CREATE TABLE law_articles (
    id UUID PRIMARY KEY,
    law_code TEXT NOT NULL,
    law_title TEXT NOT NULL,
    article_number TEXT NOT NULL,
    article_title TEXT,
    article_text TEXT NOT NULL,
    source_file TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);
```

#### `llm_logs`

Опциональная таблица для хранения LLM-вызовов, если кроме файловых логов нужно хранение в БД.

```sql
CREATE TABLE llm_logs (
    id UUID PRIMARY KEY,
    request_id UUID REFERENCES requests(id),
    step_name TEXT NOT NULL,
    model_name TEXT NOT NULL,
    prompt_name TEXT NOT NULL,
    prompt_text TEXT,
    input_json JSONB,
    raw_response TEXT,
    parsed_response JSONB,
    duration_ms INTEGER,
    error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
```

---

## 12. API endpoints

### 12.1. Анализ обращения

```http
POST /api/v1/claims/analyze
```

Request:

```json
{
  "user_text": "Купил телефон, через неделю он перестал включаться. Магазин отказывается вернуть деньги."
}
```

Response:

```json
{
  "request_id": "uuid",
  "status": "claim_generated | need_more_info | route_to_lawyer | legal_context_not_found | validation_failed | llm_error",
  "result": {...}
}
```

### 12.2. Продолжение после уточняющих вопросов

```http
POST /api/v1/claims/{request_id}/continue
```

Request:

```json
{
  "answers": {
    "opponent_name": "ООО Ромашка",
    "applicant_name": "Иванов Иван Иванович"
  }
}
```

### 12.3. Получение результата

```http
GET /api/v1/claims/{request_id}
```

### 12.4. История обращений

Так как авторизации нет, endpoint возвращает все обращения.

```http
GET /api/v1/claims
```

### 12.5. Healthcheck

```http
GET /health
```

Response:

```json
{
  "status": "ok"
}
```

---

## 13. Prompts

Промпты должны храниться отдельными файлами:

```text
prompts/fact_extractor.md
prompts/case_classifier.md
prompts/claim_evaluator.md
prompts/claim_generator.md
prompts/result_validator.md
```

### 13.1. Общие правила для всех промптов

Каждый промпт должен требовать:

1. Возвращать только валидный JSON.
2. Не использовать Markdown в ответе.
3. Не добавлять комментарии вне JSON.
4. Не выдумывать факты.
5. Если данных нет — использовать `null`, `unknown` или `[не указано]`.
6. Не ссылаться на статьи, которых нет в `LEGAL_CONTEXT`.
7. Не добавлять требования, которых пользователь не заявлял.

### 13.2. `fact_extractor.md`

Задача: извлечь факты из текста пользователя.

Должен возвращать:

1. `problem_summary`;
2. `parties`;
3. `transaction`;
4. `problem`;
5. `user_demand`;
6. `prior_contact`;
7. `documents`;
8. `missing_fields`.

### 13.3. `case_classifier.md`

Задача: определить тип кейса в рамках ЗоЗПП или вывести `route_to_lawyer`.

Должен учитывать:

1. потребительский спор или нет;
2. тип товара/услуги;
3. технически сложный товар;
4. требование пользователя;
5. уровень риска.

### 13.4. `claim_evaluator.md`

Задача: решить, можно ли сформировать досудебную претензию.

Должен возвращать:

1. `status`;
2. `confidence`;
3. `reasoning`;
4. `critical_missing_fields`;
5. `non_critical_missing_fields`;
6. `recommended_action`.

### 13.5. `claim_generator.md`

Задача: сформировать JSON претензии без шаблона.

Должен использовать только:

1. исходные факты;
2. дополненные ответы пользователя;
3. найденный `LEGAL_CONTEXT`.

### 13.6. `result_validator.md`

Задача: проверить итоговый JSON и текст претензии.

Должен искать:

1. выдуманные статьи;
2. выдуманные даты;
3. выдуманные суммы;
4. выдуманные данные сторон;
5. неправильный тип претензии;
6. требования, которых не было в исходном запросе;
7. противоречия между фактами и документом.

---

## 14. Статусы pipeline

```text
received
facts_extracted
classified
legal_context_found
legal_context_not_found
claim_applicable
need_more_info
route_to_lawyer
claim_generated
validation_failed
completed
llm_error
internal_error
```

---

## 15. Нефункциональные требования

### Производительность

На этапе MVP жестких требований по времени ответа нет.

### Локальный режим

Система должна поддерживать локальный запуск через Docker Compose.

Внешний LLM-провайдер напрямую не используется. Все модели вызываются только через LiteLLM.

### Логирование

Использовать Loguru.

Логировать:

1. входные запросы;
2. статусы pipeline;
3. LLM prompts;
4. LLM responses;
5. ошибки парсинга JSON;
6. ошибки поиска статей;
7. результат validator-модели;
8. финальный статус.

### Персональные данные

На этапе MVP специальных требований к хранению и маскированию персональных данных нет.

---

## 16. Acceptance Criteria

### AC-1. Сервис принимает свободный текст

При отправке `POST /api/v1/claims/analyze` с непустым `user_text` система создает обращение и запускает pipeline.

### AC-2. Система извлекает факты

После первого LLM-вызова в БД сохраняется структурированный JSON с фактами.

### AC-3. Система классифицирует кейс

Система определяет `case_type` и применимость ЗоЗПП.

### AC-4. Система ищет статьи в БД

Система ищет релевантные статьи в `law_articles` и передает их в `LEGAL_CONTEXT`.

### AC-5. Если статьи не найдены, генерация останавливается

При пустом `LEGAL_CONTEXT` система возвращает статус `legal_context_not_found` и объяснение ошибки.

### AC-6. Система задает уточняющие вопросы

Если обязательных данных не хватает, система возвращает `need_more_info`, уже заполненный JSON и список вопросов.

### AC-7. Система генерирует JSON претензии

Если данных достаточно и претензия применима, система возвращает `claim_generated` и объект `claim_document`.

### AC-8. Система не выдумывает правовые нормы

В `claim_document` могут использоваться только статьи из `LEGAL_CONTEXT`.

### AC-9. Результат проверяется отдельной моделью

Перед финальной выдачей результат отправляется в `LITELLM_VALIDATOR_MODEL`.

### AC-10. Validator блокирует критические ошибки

Если validator находит выдуманную статью, дату, сумму или неправильный тип претензии, система возвращает `validation_failed`.

### AC-11. Все результаты сохраняются

Система сохраняет обращение, шаги pipeline и финальный JSON в PostgreSQL.

### AC-12. Есть Docker Compose

Проект запускается локально через Docker Compose.

---

## 17. План реализации

### Этап 1. Базовый backend

1. Создать FastAPI-проект.
2. Добавить Docker Compose.
3. Добавить PostgreSQL.
4. Добавить конфиг через `.env`.
5. Добавить Loguru.
6. Добавить healthcheck endpoint.

### Этап 2. БД и модели

1. Создать таблицы `requests`, `pipeline_runs`, `claim_results`, `law_articles`.
2. Добавить Alembic migrations.
3. Добавить репозитории для чтения/записи.

### Этап 3. LiteLLM client

1. Реализовать единый `LLMClient`.
2. Добавить поддержку main, validator и fallback моделей.
3. Добавить retries и timeout.
4. Добавить логирование prompt/response.
5. Добавить JSON parsing и обработку ошибок.

### Этап 4. Prompts

1. Создать папку `prompts/`.
2. Создать 5 prompt-файлов.
3. Подключить загрузку промптов из файлов.

### Этап 5. Pipeline

1. Реализовать `FactExtractor`.
2. Реализовать `CaseClassifier`.
3. Реализовать `ConsumerLawApplicabilityChecker`.
4. Реализовать `LawRetriever`.
5. Реализовать `ClaimEvaluator`.
6. Реализовать `RequiredFieldsChecker`.
7. Реализовать `ClaimGenerator`.
8. Реализовать `ResultValidator`.

### Этап 6. Законодательная база

1. Добавить скрипт `parse_law_txt.py`.
2. Подготовить загрузку `.txt`-файлов ЗоЗПП.
3. Распарсить статьи.
4. Сохранить статьи в PostgreSQL.
5. Реализовать полнотекстовый поиск.

### Этап 7. API

1. `POST /api/v1/claims/analyze`.
2. `POST /api/v1/claims/{request_id}/continue`.
3. `GET /api/v1/claims/{request_id}`.
4. `GET /api/v1/claims`.
5. `GET /health`.

### Этап 8. Проверка вручную

1. Запустить несколько ручных примеров.
2. Проверить логи.
3. Проверить, что validator блокирует выдуманные нормы.
4. Проверить сценарий `need_more_info`.
5. Проверить сценарий `legal_context_not_found`.
6. Проверить сценарий `route_to_lawyer`.

---

## 18. Открытые вопросы на будущее

1. Добавлять ли шаблоны претензий после MVP?
2. Добавлять ли DOCX/PDF-экспорт?
3. Добавлять ли frontend?
4. Добавлять ли авторизацию?
5. Добавлять ли юридическую проверку человеком?
6. Добавлять ли подбор юриста?
7. Добавлять ли другие кодексы и законы?
8. Добавлять ли pgvector и embeddings?
9. Добавлять ли обработку документов?
10. Добавлять ли тестовый набор и golden tests?

---

## 19. Краткое резюме MVP

MVP — это внутренний web-прототип юридического RAG-сервиса.

Он принимает свободный текст, анализирует его через LiteLLM, ищет релевантные статьи Закона РФ «О защите прав потребителей», формирует JSON досудебной претензии, задает уточняющие вопросы при нехватке данных и валидирует результат отдельной LLM-моделью.

Главный принцип MVP: система не должна выдумывать нормы, даты, суммы и факты. Если релевантные статьи не найдены или данных недостаточно, генерация должна быть остановлена или переведена в уточняющие вопросы.
